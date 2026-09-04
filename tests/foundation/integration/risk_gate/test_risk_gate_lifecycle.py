"""FND-06 Risk & Safety Gate 통합테스트 — 실제 dev DB 대상. 48번 §5/78번 §6 중
FND-07(paper_control)/order adapter 없이 재현 가능한 범위(RSK-001~005)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.observability.context import bind as bind_request_context
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
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
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user


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
    return PostgresRiskGateRepository(pool)


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
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


async def _tenant(pool):
    return await create_test_user(pool)


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
    검증한다 — 실제 pre-submit 게이트 배선은 FND-07 이후."""
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

    results = await asyncio.gather(*[_activate() for _ in range(5)])
    tokens = sorted(r.fence_token for r in results)
    assert tokens == sorted(set(tokens)), "fence token이 중복됐다 — 유실된 증가가 있다"
    assert len(tokens) == 5


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


async def test_activate_and_deactivate_safety_control_record_audit_events(
    pool, repo, audit_repo
):
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


# --- R-34: f4b9d6e5a7c8 (gate_kind 6종 + trace_id + idempotency_digest +
# paused_by_control_id) ---------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


async def _gate_kind_check_def(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'risk_evaluation'::regclass "
            "AND conname = 'risk_evaluation_gate_kind_check'"
        )
    assert row is not None
    return row["def"]


async def _column_exists(pool: asyncpg.Pool, table_name: str, column_name: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2",
            table_name,
            column_name,
        )
    return value is not None


async def test_gate_kind_check_rejects_value_outside_the_six(pool):
    """DoD(2)/(4) — 6종 목록 밖의 값은 CHECK로 거부돼야 한다(임의 추가·개명
    금지의 반대 방향 검증: 목록에 없는 값도 통과시키면 안 된다)."""
    tenant_id = await _tenant(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO risk_evaluation "
                "(tenant_id, gate_kind, subject_fingerprint, outcome, rule_version, expires_at) "
                "VALUES ($1, 'NOT_A_GATE_KIND', 'fp', 'ALLOW', 'v1', now())",
                tenant_id,
            )


async def test_gate_kind_check_accepts_all_six_values(pool):
    """DoD(4) — 6종 전부가 실제로 통과해야 한다(명세 §3.8/§5 열거 그대로).

    삽입한 행은 테스트가 끝나면 반드시 지운다 — PRE_TRADE/PRE_SUBMIT/
    INTRADAY/RECOVERY 값 행이 커밋된 채 남으면, 옛 2종 CHECK로 되돌아가는
    `test_migration_round_trip_restores_gate_kinds_and_new_columns`의
    downgrade가 그 행들 때문에 실패한다(공유 테스트 DB)."""
    tenant_id = await _tenant(pool)
    gate_kinds = ("DEPLOYMENT", "PRE_INTENT", "PRE_TRADE", "PRE_SUBMIT", "INTRADAY", "RECOVERY")
    inserted_ids: list[UUID] = []
    try:
        for gate_kind in gate_kinds:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO risk_evaluation "
                    "(tenant_id, gate_kind, subject_fingerprint, outcome, rule_version, "
                    "expires_at) "
                    "VALUES ($1, $2, $3, 'ALLOW', 'v1', now()) RETURNING id",
                    tenant_id,
                    gate_kind,
                    f"fp-{gate_kind}",
                )
            assert row is not None
            inserted_ids.append(row["id"])
    finally:
        if inserted_ids:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM risk_evaluation WHERE id = ANY($1::uuid[])", inserted_ids
                )


async def test_idempotency_digest_unique_rejects_duplicate(pool):
    """DoD(2) — safety_control.idempotency_digest UNIQUE 위반은 거부돼야 한다."""
    tenant_id = await _tenant(pool)
    # tenant_id로 매 실행마다 다른 값 — 고정 문자열이면 이전 실행이 커밋해
    # 남긴 행과 재실행 시 충돌한다.
    digest = hashlib.sha256(f"r-34-dup-digest:{tenant_id}".encode()).hexdigest()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO safety_control "
                "(scope, scope_ref, reason, actor_subject_id, fence_token, idempotency_digest) "
                "VALUES ('ACCOUNT', $1, 'r1', $2, 1, $3)",
                str(tenant_id),
                tenant_id,
                digest,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO safety_control "
                    "(scope, scope_ref, reason, actor_subject_id, fence_token, idempotency_digest) "
                    "VALUES ('ACCOUNT', $1, 'r2', $2, 2, $3)",
                    str(tenant_id),
                    tenant_id,
                    digest,
                )
        finally:
            await conn.execute(
                "DELETE FROM safety_control WHERE idempotency_digest = $1", digest
            )


async def test_paused_by_control_id_fk_rejects_unknown_control(pool):
    """DoD(2) — strategy_executions.paused_by_control_id FK는 존재하지 않는
    safety_control.id를 거부해야 한다."""
    tenant_id = await _tenant(pool)
    strategy_id = f"r34-test-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent, lifecycle_status) "
            "VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb, "
            " 'test-author', 'APPROVED')",
            strategy_id,
            tenant_id,
            json.dumps({}),
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO strategy_executions "
                "(strategy_id, strategy_version, user_id, exchange, mode, "
                " allocated_capital, currency, status, paused_by_control_id) "
                "VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 1000, 'USDT', 'RUNNING', $3)",
                strategy_id,
                tenant_id,
                uuid4(),
            )


async def test_evaluate_risk_gate_always_records_trace_id_from_context(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """DoD(3) — evaluate_risk_gate()의 신규 경로는 PLT-01 관측 컨텍스트의
    trace_id를 항상 채워 risk_evaluation에 기록한다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    with bind_request_context() as ctx:
        expected_trace_id = ctx.trace_id
        result = await evaluate_risk_gate(
            repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
        )

    assert result.trace_id == expected_trace_id
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT trace_id FROM risk_evaluation WHERE id = $1", result.id)
    assert row is not None
    assert row["trace_id"] == expected_trace_id


async def test_migration_round_trip_restores_gate_kinds_and_new_columns(pool):
    """DoD(2) — upgrade→downgrade→upgrade 왕복을 실DB로 재현: downgrade는
    3개 신규 컬럼을 지우고 CHECK를 옛 2종으로 되돌리며, 재차 upgrade하면
    정확히 원래(6종 + 3개 컬럼) 상태로 복원돼야 한다."""
    try:
        before = await _gate_kind_check_def(pool)
        assert "PRE_SUBMIT" in before
        assert "INTRADAY" in before
        assert "RECOVERY" in before
        assert await _column_exists(pool, "risk_evaluation", "trace_id")
        assert await _column_exists(pool, "safety_control", "idempotency_digest")
        assert await _column_exists(pool, "strategy_executions", "paused_by_control_id")

        _run_alembic("downgrade", "c7e6a3b2d4f5")

        after_downgrade = await _gate_kind_check_def(pool)
        assert "PRE_SUBMIT" not in after_downgrade
        assert "INTRADAY" not in after_downgrade
        assert "RECOVERY" not in after_downgrade
        assert not await _column_exists(pool, "risk_evaluation", "trace_id")
        assert not await _column_exists(pool, "safety_control", "idempotency_digest")
        assert not await _column_exists(pool, "strategy_executions", "paused_by_control_id")

        _run_alembic("upgrade", "head")

        after_upgrade = await _gate_kind_check_def(pool)
        assert after_upgrade == before
        assert await _column_exists(pool, "risk_evaluation", "trace_id")
        assert await _column_exists(pool, "safety_control", "idempotency_digest")
        assert await _column_exists(pool, "strategy_executions", "paused_by_control_id")
    finally:
        _run_alembic("upgrade", "head")
