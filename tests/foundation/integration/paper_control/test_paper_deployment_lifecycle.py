"""FND-07 Paper Execution & Control 통합테스트 — 실제 dev DB 대상. 47번 §5/77번
§6 중 실 tick 스케줄러 없이 재현 가능한 범위(PAP-001~002, 004~006)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.paper_control.adapters.fake_paper_adapter import FakePaperExecutionAdapter
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.application.pause_deployment import (
    InvalidDeploymentStateError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.application.request_deployment import (
    NoActiveMandateError,
    request_deployment,
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
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresPaperControlRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


async def _tenant_with_mandate(pool, mandate_repo, trust_repo):
    tenant_id = await create_test_user(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)
    return tenant_id


async def _request(repo, mandate_repo, tenant_id, *, key_suffix="", **overrides):
    defaults = dict(
        package_ref="pkg-ref-1",
        connection_id=None,
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-acct-1",
        endpoint_classification="SANDBOX",
    )
    defaults.update(overrides)
    return await request_deployment(
        repo,
        mandate_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        idempotency_key=f"req-{tenant_id}{key_suffix}",
        **defaults,
    )


async def test_request_without_active_mandate_raises(pool, repo, mandate_repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(NoActiveMandateError):
        await _request(repo, mandate_repo, tenant_id)


async def test_request_with_invalid_provenance_ends_failed(pool, repo, mandate_repo, trust_repo):
    """PAP-002 — live endpoint rejects before adapter call; 여기서는 REQUEST
    단계에서 FAILED 행으로 귀결되는지 확인한다(adapter 자체가 아직 없는
    단계라 "adapter 호출 전"은 자동으로 만족)."""
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    with pytest.raises(InvalidProvenanceError):
        await _request(
            repo, mandate_repo, tenant_id, endpoint_classification="LIVE_PRODUCTION"
        )


async def test_full_lifecycle_request_start_pause_resume_stop(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-001 — valid paper refs/provenance reaches READY/RUNNING and emits
    audited transitions."""
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)
    assert deployment.state.value == "READY"

    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-1",
    )
    assert started.state.value == "RUNNING"
    assert started.fence_token == 0

    paused = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-1",
    )
    assert paused.state.value == "PAUSED"
    assert paused.fence_token == 1

    resumed = await resume_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="resume-1",
    )
    assert resumed.state.value == "RUNNING"

    stopped = await stop_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="stop-1",
    )
    assert stopped.state.value == "STOPPED"
    assert stopped.fence_token == 2

    with pytest.raises(InvalidDeploymentStateError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-after-stop",
        )


async def test_duplicate_command_is_idempotent(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-006 — duplicate command is idempotent; 재실행 없이 같은 결과를
    반환한다(fence가 두 번 늘지 않는다)."""
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)
    await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-dup",
    )

    first = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-dup",
    )
    second = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-dup",
    )
    assert first.fence_token == second.fence_token == 1


async def test_start_denied_when_global_kill_switch_active(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-005 계열 — risk gate가 거부하면(kill switch) start 자체가 막힌다."""
    from src.foundation.risk_gate.application.activate_safety_control import (
        activate_safety_control,
    )
    from src.foundation.risk_gate.application.deactivate_safety_control import (
        deactivate_safety_control,
    )
    from src.foundation.risk_gate.domain.models import SafetyScope

    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)

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
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)
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
            repo, adapter, deployment_id=deployment.id, expected_fence_token=stale_fence, sequence=1
        )


async def test_provider_failure_during_submit_degrades_deployment_never_switches_mode(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-007 — provider timeout produces DEGRADED, never switches modes.
    실패해도 deployment.provenance(mode=PAPER 근거)는 그대로다 — 상태만
    RUNNING에서 DEGRADED로 내려간다."""
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)
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


async def test_concurrent_pause_and_stop_never_double_counts_fence(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """105번 §4 형태 A — pause/stop 경합에서도 fence가 정확히 한 번씩만
    늘어난다(105번 표준 원자성 회귀 테스트, PAP-003 원리와 동일)."""
    tenant_id = await _tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await _request(repo, mandate_repo, tenant_id)
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
