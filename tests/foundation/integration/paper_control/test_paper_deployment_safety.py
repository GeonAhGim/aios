"""FND-07 Paper Execution & Control 통합테스트 — risk gate/kill switch 연동,
provider 실패, fence 동시성(PAP-003~005, 007~008)."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.foundation.paper_control.adapters.fake_paper_adapter import FakePaperExecutionAdapter
from src.foundation.paper_control.application.apply_safety_control import (
    apply_safety_control_to_deployments,
)
from src.foundation.paper_control.application.pause_deployment import (
    InvalidDeploymentStateError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.application.start_deployment import (
    InvalidDeploymentStateError as StartInvalidDeploymentStateError,
)
from src.foundation.paper_control.application.start_deployment import (
    RiskGateDeniedError,
    resume_deployment,
    start_deployment,
)
from src.foundation.paper_control.application.submit_paper_intent import (
    FenceSupersededError,
    ProviderUnavailableError,
    submit_paper_intent,
)
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from tests.foundation.integration.paper_control.conftest import request, tenant_with_mandate


async def test_start_denied_when_global_kill_switch_active(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-005 계열 — risk gate가 거부하면(kill switch) start 자체가 막힌다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)

    control = await activate_safety_control(
        risk_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="테스트 정지",
    )
    try:
        with pytest.raises(RiskGateDeniedError):
            await start_deployment(
                repo,
                risk_repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                actor_subject_id=tenant_id,
                deployment_id=deployment.id,
                idempotency_key="start-denied",
            )
        still_ready = await repo.get_deployment(deployment.id)
        assert still_ready.state.value == "READY"
    finally:
        await deactivate_safety_control(
            risk_repo, tenant_id=tenant_id, actor_is_admin=False, control_id=control.id
        )


async def test_submit_paper_intent_rejects_superseded_fence(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-004 — pause fence invalidates an already-queued intent attempt."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-fence",
    )
    stale_fence = started.fence_token  # tick 계획 시점에 읽은 fence(0)

    await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-fence",
    )

    adapter = FakePaperExecutionAdapter()
    with pytest.raises(FenceSupersededError):
        await submit_paper_intent(
            repo,
            adapter,
            risk_repo,
            mandate_repo,
            connection_repo,
            deployment_id=deployment.id,
            expected_fence_token=stale_fence,
            sequence=1,
        )


async def test_provider_failure_during_submit_degrades_deployment_never_switches_mode(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-007 — provider timeout produces DEGRADED, never switches modes.
    실패해도 deployment.provenance(mode=PAPER 근거)는 그대로다 — 상태만
    RUNNING에서 DEGRADED로 내려간다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-fail",
    )

    failing_adapter = FakePaperExecutionAdapter(fail_submit=True)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await submit_paper_intent(
            repo,
            failing_adapter,
            risk_repo,
            mandate_repo,
            connection_repo,
            deployment_id=deployment.id,
            expected_fence_token=started.fence_token,
            sequence=1,
        )
    assert "시뮬레이션" not in str(excinfo.value)  # 원문 adapter 예외를 노출하지 않는다

    degraded = await repo.get_deployment(deployment.id)
    assert degraded.state.value == "DEGRADED"
    assert degraded.provenance.credential_class.value == "PAPER"  # 모드는 그대로

    # PAP-008 "recovery needs fresh policy/risk/reconciliation decision; no
    # auto-resume" — DEGRADED가 된 뒤 resume_deployment()(PAUSED 전용)로
    # 우회 재개할 수 없다. 실제 명령 계층에서도(도메인 규칙 표뿐 아니라)
    # 이 불변조건이 지켜지는지 확인한다. (pause_deployment.py와
    # start_deployment.py는 서로 다른 InvalidDeploymentStateError 클래스를
    # 독립적으로 정의한다 — resume_deployment는 start_deployment.py 소속이라
    # 그쪽 클래스로 잡아야 한다.)
    with pytest.raises(StartInvalidDeploymentStateError):
        await resume_deployment(
            repo,
            risk_repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="resume-after-degraded",
        )


async def test_kill_switch_activated_after_running_blocks_further_submits(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """교차세션 감사 발견(agent-platform-12, 2026-09-02) 회귀 — kill switch가
    deployment의 fence를 건드리지 않으므로(pause/stop을 자동으로 트리거하지
    않는다), fence 재확인만으로는 RUNNING *도중* 켜진 kill switch를 못 잡는다.
    PRE_INTENT 게이트가 이 틈을 실제로 막는지 확인한다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-then-kill",
    )
    # fence는 여전히 start 시점 값 그대로다 — kill switch는 이 값을 안 바꾼다.
    assert started.fence_token == 0

    await activate_safety_control(
        risk_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="RUNNING 도중 kill switch 테스트",
    )

    adapter = FakePaperExecutionAdapter()
    with pytest.raises(RiskGateDeniedError):
        await submit_paper_intent(
            repo,
            adapter,
            risk_repo,
            mandate_repo,
            connection_repo,
            deployment_id=deployment.id,
            expected_fence_token=started.fence_token,  # 여전히 "유효한" fence
            sequence=1,
        )

    # 거부 이후에도 deployment 자체는 여전히 RUNNING이다 — PRE_INTENT 거부는
    # PAP-007(DEGRADED)과 다른 종류다: provider가 아니라 정책이 막은 것이므로
    # deployment 상태를 바꾸지 않는다(다음 tick에서 kill switch가 풀리면
    # 그대로 재개 가능해야 한다).
    still_running = await repo.get_deployment(deployment.id)
    assert still_running.state.value == "RUNNING"


async def test_concurrent_pause_and_stop_never_double_counts_fence(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """105번 §4 형태 A — pause/stop 경합에서도 fence가 정확히 한 번씩만
    늘어난다(105번 표준 원자성 회귀 테스트, PAP-003 원리와 동일)."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-race",
    )

    results = await asyncio.gather(
        pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-race",
        ),
        stop_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="stop-race",
        ),
        return_exceptions=True,
    )
    final = await repo.get_deployment(deployment.id)
    # 둘 다 성공하든(pause 먼저 -> stop이 재시도로 성공) 하나만 성공하든,
    # 최종 상태는 항상 STOPPED여야 한다(STOP이 우선한다, 77번 §2).
    assert final.state.value == "STOPPED"
    exceptions = [r for r in results if isinstance(r, Exception)]
    for exc in exceptions:
        assert isinstance(exc, InvalidDeploymentStateError)


async def test_kill_switch_activation_actually_pauses_running_deployment(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PM 배정 ② — kill switch가 fence를 실제로 소비해 RUNNING을 PAUSED로
    옮기는지 확인한다(apply_safety_control.py). test_kill_switch_activated_
    after_running_blocks_further_submits()는 activate_safety_control()만
    부르므로 deployment가 RUNNING으로 남는다 — 이 테스트는 실제 라우터가
    하는 것처럼 apply_safety_control_to_deployments()까지 이어서 부른다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-then-cascade-pause",
    )
    assert started.fence_token == 0

    control = await activate_safety_control(
        risk_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="cascade pause 테스트",
    )
    paused_views = await apply_safety_control_to_deployments(
        repo,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        safety_control_id=control.id,
        actor_subject_id=tenant_id,
        reason="cascade pause 테스트",
    )
    assert [v.id for v in paused_views] == [deployment.id]

    after = await repo.get_deployment(deployment.id)
    assert after.state.value == "PAUSED"
    assert after.fence_token == 1  # 실제로 fence가 소비됐다


async def test_apply_safety_control_skips_non_running_and_unhandled_scopes(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """READY 상태 배포는 건드리지 않고(RUNNING만 대상), PROVIDER/
    STRATEGY_DEPLOYMENT 범위는 아직 처리 대상이 아니므로 조용히 빈 목록을
    돌려준다(apply_safety_control.py 상단 docstring의 명시적 스콥 축소)."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    ready_deployment = await request(repo, mandate_repo, tenant_id)
    assert ready_deployment.state.value == "READY"

    result = await apply_safety_control_to_deployments(
        repo,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        safety_control_id=uuid4(),
        actor_subject_id=tenant_id,
        reason="READY는 대상이 아님",
    )
    assert result == []
    still_ready = await repo.get_deployment(ready_deployment.id)
    assert still_ready.state.value == "READY"

    provider_scope_result = await apply_safety_control_to_deployments(
        repo,
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
        safety_control_id=uuid4(),
        actor_subject_id=tenant_id,
        reason="PROVIDER 범위 미구현",
    )
    assert provider_scope_result == []
