"""FND-06 Risk & Safety Gate 통합테스트 — 실제 dev DB 대상. 48번 §5/78번 §6 중
FND-07(paper_control)/order adapter 없이 재현 가능한 범위(RSK-001~005)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.risk_gate.application.activate_safety_control import (
    MissingScopeRefError,
    UnauthorizedSafetyControlScopeError,
    activate_safety_control,
)
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind, SafetyScope
from tests.foundation.integration.risk_gate.conftest import _tenant, activate_mandate_with_defaults


class _FakeHealthyConnectionRepo:
    """provider_code를 고정으로 돌려주는 최소 fake — 실제 connection
    lifecycle(begin/confirm, MFA/consent) 없이 evaluate_risk_gate()의
    provider_code 전달 경로만 검증하기 위함(#2026-09-02-27)."""

    def __init__(self, *, tenant_id: UUID, provider_code: str) -> None:
        self._connection = AccountConnection(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_subject_id=tenant_id,
            provider_code=provider_code,
            opaque_account_ref="ACCT-TEST",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=(),
            revision=1,
        )

    async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
        return self._connection if connection_id == self._connection.id else None

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        return ConnectionHealth(
            connection_id=connection_id,
            evaluated_at=datetime.now(timezone.utc),
            state=HealthState.HEALTHY,
        )

    @property
    def connection_id(self) -> UUID:
        return self._connection.id


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


async def test_evaluate_denies_when_no_active_mandate(pool, repo, mandate_repo, connection_repo):
    """RSK-002 — missing input yields DENY, never implicit ALLOW."""
    tenant_id = await _tenant(pool)
    result = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert result.outcome.value == "DENY"
    assert "RISK_INPUT_MANDATE_MISSING" in result.reason_codes


async def test_evaluate_allows_with_active_mandate_and_no_controls(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """RSK-001 — pinned input/rule produces stable decision."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    first = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert first.outcome.value == "ALLOW"

    # 짧은 TTL 캐시 안에서 같은 입력이면 같은 evaluation을 재사용한다.
    second = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert second.id == first.id


async def test_active_kill_switch_denies_even_with_healthy_mandate(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """RSK-003/48번 §5 acceptance test 1 — 어느 게이트든 kill switch가 최종
    거부권을 갖는다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="사용자 자진 정지 테스트",
    )

    result = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert result.outcome.value == "DENY"
    assert "RISK_KILL_SWITCH_ACTIVE_ACCOUNT" in result.reason_codes


async def test_global_kill_switch_denies_every_tenant(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """48번 §5 acceptance test 4 — global kill switch blocks every tenant's
    new orders."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_a)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_b)

    admin_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=admin_id,
        actor_subject_id=admin_id,
        actor_is_admin=True,
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason="글로벌 정지 테스트",
    )
    try:
        for tenant_id in (tenant_a, tenant_b):
            result = await evaluate_risk_gate(
                repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                gate_kind=GateKind.DEPLOYMENT,
            )
            assert result.outcome.value == "DENY"
            assert "RISK_KILL_SWITCH_ACTIVE_GLOBAL" in result.reason_codes
    finally:
        # GLOBAL 통제는 실제로 전역이라, 안 끄고 두면 이 공유 테스트 DB의
        # 다른 모든 이후 테스트(다른 파일 포함)까지 항상 DENY로 오염시킨다.
        await deactivate_safety_control(
            repo, tenant_id=admin_id, actor_is_admin=True, control_id=control.id
        )


async def test_strategy_deployment_scope_without_control_keeps_existing_behavior(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """task-9064 negative — a STRATEGY_DEPLOYMENT id passed to evaluate_risk_gate
    with no matching control active must not change the outcome, even when a
    different scope (ACCOUNT) control is active. Only the deployment's own
    scope_ref may deny it."""
    tenant_id = await _tenant(pool)
    deployment_id = uuid4()
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    other_tenant_id = await _tenant(pool)
    other_control = await activate_safety_control(
        repo,
        tenant_id=other_tenant_id,
        actor_subject_id=other_tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(other_tenant_id),
        reason="다른 tenant의 무관한 통제",
    )
    try:
        result = await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
            strategy_deployment_id=deployment_id,
        )
        assert result.outcome.value == "ALLOW"
    finally:
        await deactivate_safety_control(
            repo, tenant_id=other_tenant_id, actor_is_admin=False, control_id=other_control.id
        )


async def test_strategy_deployment_kill_switch_denies_that_deployment(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """task-9064 (audit F3, #2026-09-02-28) — a STRATEGY_DEPLOYMENT-scoped
    kill switch must be looked up by evaluate_risk_gate when a
    strategy_deployment_id is passed, and must DENY + record a WORM
    risk_evaluation row."""
    tenant_id = await _tenant(pool)
    deployment_id = uuid4()
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    baseline = await evaluate_risk_gate(
        repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        gate_kind=GateKind.DEPLOYMENT,
        strategy_deployment_id=deployment_id,
    )
    assert baseline.outcome.value == "ALLOW"

    admin_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=admin_id,
        actor_subject_id=admin_id,
        actor_is_admin=True,
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=str(deployment_id),
        reason="배포 단위 킬스위치 테스트",
    )
    try:
        # activate_safety_control()은 STRATEGY_DEPLOYMENT 범위에 대해서는
        # (아직 tenant_id를 몰라) 캐시를 자동 무효화하지 않는다
        # (activate_safety_control.py 주석 참조) — 여기서는 TTL 만료를
        # 기다리지 않고 이 리프가 고치는 조회 경로 자체를 검증하기 위해
        # 직접 무효화한다.
        await repo.invalidate_evaluations(tenant_id=tenant_id)

        result = await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
            strategy_deployment_id=deployment_id,
        )
        assert result.outcome.value == "DENY"
        assert "RISK_KILL_SWITCH_ACTIVE_STRATEGY_DEPLOYMENT" in result.reason_codes
        assert result.id != baseline.id

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT outcome, reason_codes FROM risk_evaluation WHERE id = $1", result.id
            )
        assert row is not None
        assert row["outcome"] == "DENY"
        assert "RISK_KILL_SWITCH_ACTIVE_STRATEGY_DEPLOYMENT" in row["reason_codes"]

        # 다른 배포는 이 control의 영향을 받지 않는다.
        other_deployment_id = uuid4()
        unaffected = await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
            strategy_deployment_id=other_deployment_id,
        )
        assert unaffected.outcome.value == "ALLOW"
    finally:
        await deactivate_safety_control(
            repo, tenant_id=admin_id, actor_is_admin=True, control_id=control.id
        )


async def test_self_service_cannot_activate_tenant_wide_control(pool, repo):
    tenant_id = await _tenant(pool)
    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.TENANT,
            scope_ref=str(tenant_id),
            reason="권한 없는 시도",
        )


async def test_self_service_cannot_activate_another_accounts_control(pool, repo):
    tenant_id = await _tenant(pool)
    other_id = await _tenant(pool)
    with pytest.raises(UnauthorizedSafetyControlScopeError):
        await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(other_id),
            reason="다른 계좌를 지정하려는 시도",
        )


async def test_deactivate_marks_inactive_and_is_idempotent_failure(pool, repo):
    tenant_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="해제 테스트",
    )
    deactivated = await deactivate_safety_control(
        repo, tenant_id=tenant_id, actor_is_admin=False, control_id=control.id
    )
    assert deactivated.state.value == "INACTIVE"

    with pytest.raises(ConcurrencyConflictError):
        await deactivate_safety_control(
            repo, tenant_id=tenant_id, actor_is_admin=False, control_id=control.id
        )


async def test_concurrent_activations_never_lose_a_fence_token(pool, repo):
    """RSK-005 — activate control races with submit and fence prevents
    post-control side effect. 여기서는 그 전제조건(fence 증가 자체가
    동시 요청에서도 유실 없이 유일해야 한다)을 105번 §4 형태 A로
    검증한다 — 실제 pre-submit 게이트 배선은 FND-07 이후.

    task-9065(F4(L)) — 같은 (scope, scope_ref)에 대한 동시 activate는 이제
    `insert_safety_control()`의 `SELECT ... FOR UPDATE` 사전 검사에 걸려,
    커밋 순서상 앞선 것만 성공하고 나머지는 `ConcurrencyConflictError`로
    거부된다(이전에는 5개 모두 성공해 ACTIVE 행이 중복 생성됐다 — 그게 이
    리프가 고치는 버그였다). 성공한 것들의 fence_token이 유일한지, 그리고
    거부된 시도가 fence를 오염시키지 않았는지를 확인한다."""
    tenant_id = await _tenant(pool)

    async def _activate():
        return await activate_safety_control(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant_id),
            reason="동시성 테스트",
        )

    results = await asyncio.gather(*[_activate() for _ in range(5)], return_exceptions=True)
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) >= 1, "적어도 하나는 성공해야 한다"
    assert all(isinstance(f, ConcurrencyConflictError) for f in failures)
    tokens = sorted(r.fence_token for r in successes)
    assert tokens == sorted(set(tokens)), "fence token이 중복됐다 — 유실된 증가가 있다"


async def test_kill_switch_after_cached_allow_takes_effect_immediately(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """레드팀 #2026-09-02-26 회귀 테스트 — ALLOW가 캐시된 뒤 킬스위치가
    걸리면, 캐시 TTL(10초)이 끝나길 기다리지 않고 같은 fingerprint로
    다시 평가해도 즉시 DENY여야 한다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    first = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert first.outcome.value == "ALLOW"  # 이 시점에 캐시됨

    await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="캐시 무효화 회귀 테스트",
    )

    second = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert second.outcome.value == "DENY"
    assert second.id != first.id  # 캐시가 무효화돼 새로 평가됐음을 방증


class _FailingMandateRepo:
    """H-11 fail-closed 회귀용 fake — mandate 저장소 조회 자체가 실패했을 때
    (예: DB 단절) risk_gate가 이미 캐시된(그리고 이제 검증 불가능한) ALLOW로
    조용히 넘어가지 않고 예외를 그대로 전파하는지 확인한다. `evaluate_risk_gate`
    는 캐시 조회보다 먼저 mandate 상태를 읽어 fingerprint를 계산하므로, 이
    조회가 실패하면 캐시 히트 여부를 확인하기도 전에 예외가 난다 — 암묵적
    ALLOW로 뭉개지는 경로가 없다는 뜻이다."""

    async def get_mandate(self, tenant_id):  # noqa: ANN001, ANN201
        raise RuntimeError("mandate store unavailable")

    async def get_revision(self, revision_id):  # noqa: ANN001, ANN201
        # Stub — 이 테스트는 get_mandate 실패 경로만 타며 get_revision은
        # 호출되지 않음. 캐시 무효화 회귀 테스트에서 mandate 조회 자체가
        # 실패하면 예외가 전파됨을 확인한다.
        return None


async def test_mandate_lookup_failure_never_falls_back_to_cached_allow(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """H-11 DoD — 무효화(여기서는 mandate 상태 확인 자체) 실패 시 fail-closed:
    캐시에 유효한 ALLOW가 있어도 mandate 저장소를 읽을 수 없으면 그 캐시를
    신뢰하지 않고 예외를 전파해야 한다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    warm = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert warm.outcome.value == "ALLOW"

    with pytest.raises(RuntimeError):
        await evaluate_risk_gate(
            repo,
            _FailingMandateRepo(),
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
        )


async def test_pause_mandate_immediately_denies_next_risk_gate_evaluation(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """H-11 DoD — pause 직후 0ms에 REJECT: `pause_mandate` 커맨드를(라우터의
    명시적 `risk_gate_repo.invalidate_evaluations()` 호출 없이) 직접 호출해도,
    이미 데워진 risk_gate ALLOW 캐시를 곧바로 재사용하지 않아야 한다.
    `subject_fingerprint`가 이제 mandate revision id+state를 포함하므로,
    pause가 커밋되는 순간 fingerprint 자체가 바뀌어 옛 캐시 행은 더 이상
    조회되지 않는다 — 별도 무효화 호출이나 TTL 만료를 기다릴 필요가 없다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    warm = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert warm.outcome.value == "ALLOW"

    await pause_mandate(mandate_repo, tenant_id=tenant_id)

    result = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert result.outcome.value == "DENY"
    assert result.id != warm.id


async def test_concurrent_pause_and_evaluate_never_serves_stale_allow_once_paused(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """H-11 DoD — 동시 pause+submit 경합: pause 커맨드와 여러 번의 risk_gate
    재평가를 동시에 실행한다. 경합 도중(아직 pause가 커밋되기 전) 평가가
    ALLOW를 받는 것은 TOCTOU상 허용되지만, pause가 완료된 *이후* 실행되는
    어떤 평가도 그 이전에 데워진 stale ALLOW를 돌려받아서는 안 된다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    warm = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert warm.outcome.value == "ALLOW"

    async def _submit_loop() -> list[str]:
        outcomes = []
        for _ in range(20):
            r = await evaluate_risk_gate(
                repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                gate_kind=GateKind.DEPLOYMENT,
            )
            outcomes.append(r.outcome.value)
        return outcomes

    await asyncio.gather(
        pause_mandate(mandate_repo, tenant_id=tenant_id),
        _submit_loop(),
    )

    after = await evaluate_risk_gate(
        repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
    )
    assert after.outcome.value == "DENY"


async def test_global_kill_switch_invalidates_cached_allow_for_every_tenant(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """#2026-09-02-26 — GLOBAL 범위는 tenant 하나가 아니라 캐시 전체를
    무효화해야 한다."""
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_a)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_b)

    for tenant_id in (tenant_a, tenant_b):
        cached = await evaluate_risk_gate(
            repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
        )
        assert cached.outcome.value == "ALLOW"

    admin_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=admin_id,
        actor_subject_id=admin_id,
        actor_is_admin=True,
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason="글로벌 캐시 무효화 테스트",
    )
    try:
        for tenant_id in (tenant_a, tenant_b):
            result = await evaluate_risk_gate(
                repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                gate_kind=GateKind.DEPLOYMENT,
            )
            assert result.outcome.value == "DENY"
    finally:
        await deactivate_safety_control(
            repo, tenant_id=admin_id, actor_is_admin=True, control_id=control.id
        )


async def test_provider_scope_kill_switch_denies_connection_on_that_provider(
    pool, repo, mandate_repo, trust_repo
):
    """레드팀 #2026-09-02-27 회귀 테스트 — PROVIDER 범위 킬스위치가 그
    provider의 connection에 대한 평가를 실제로 막아야 한다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)
    connection_repo = _FakeHealthyConnectionRepo(tenant_id=tenant_id, provider_code="binance")

    baseline = await evaluate_risk_gate(
        repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        gate_kind=GateKind.DEPLOYMENT,
        connection_id=connection_repo.connection_id,
    )
    assert baseline.outcome.value == "ALLOW"

    admin_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=admin_id,
        actor_subject_id=admin_id,
        actor_is_admin=True,
        scope=SafetyScope.PROVIDER,
        scope_ref="binance",
        reason="거래소 장애 대응 테스트",
    )
    try:
        result = await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.DEPLOYMENT,
            connection_id=connection_repo.connection_id,
        )
        assert result.outcome.value == "DENY"
        assert "RISK_KILL_SWITCH_ACTIVE_PROVIDER" in result.reason_codes
    finally:
        await deactivate_safety_control(
            repo, tenant_id=admin_id, actor_is_admin=True, control_id=control.id
        )


async def test_activate_missing_scope_ref_for_tenant_scope_is_rejected(pool, repo):
    """레드팀 #2026-09-02-29 회귀 테스트 — scope_ref 없이는 절대 매치될
    수 없는 고아 control이 조용히 생성되던 문제."""
    admin_id = await _tenant(pool)
    with pytest.raises(MissingScopeRefError):
        await activate_safety_control(
            repo,
            tenant_id=admin_id,
            actor_subject_id=admin_id,
            actor_is_admin=True,
            scope=SafetyScope.TENANT,
            scope_ref=None,
            reason="scope_ref 누락 테스트",
        )


async def test_activate_and_deactivate_safety_control_record_audit_events(pool, repo, audit_repo):
    """전수감사 §6 — safety control 활성화/비활성화가 실제 감사 이벤트를
    남기는지 확인(append_audit_event 호출자 0이던 문제의 회귀 테스트)."""
    tenant_id = await _tenant(pool)
    control = await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="감사 이벤트 테스트",
        audit_repo=audit_repo,
    )

    activated_page = await get_audit_timeline(audit_repo, tenant_id=tenant_id, limit=10)
    assert any(
        e.action == "safety_control_activated" and e.aggregate_id == control.id
        for e in activated_page.items
    )

    await deactivate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_is_admin=False,
        control_id=control.id,
        audit_repo=audit_repo,
    )

    deactivated_page = await get_audit_timeline(audit_repo, tenant_id=tenant_id, limit=10)
    assert any(
        e.action == "safety_control_deactivated" and e.aggregate_id == control.id
        for e in deactivated_page.items
    )
