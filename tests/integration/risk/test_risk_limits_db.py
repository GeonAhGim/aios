"""R-26 `risk_limit`/`risk_limit_breach` 통합테스트. 실 DB(`TEST_DATABASE_URL`)
대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-26.
DoD: (1) 표현식 UNIQUE(플랫폼 기본값 tenant_id NULL 포함) 실증, (2)
`list_effective` 교차 테넌트 0건, (3) `upsert` 낙관적 잠금(기대값 불일치 →
`ConcurrencyConflictError`, 0행 RETURNING을 성공으로 위장하지 않음), (4)
`upsert_risk_limit`은 운영자·risk officer가 아니면 거부 + tenant 스코프 검증
+ 감사 1행, (5) `limit_value` 음수·미지 scope/metric은 CHECK로 거부.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.risk_gate.adapters.postgres_limit_repository import PostgresLimitRepository
from src.foundation.risk_gate.application.upsert_risk_limit import (
    CrossTenantLimitScopeError,
    LimitActor,
    UnauthorizedLimitActorError,
    upsert_risk_limit,
)
from src.foundation.risk_gate.domain.models import LimitMetric, LimitScope, RiskLimit
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresLimitRepository(pool)


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


@pytest.fixture
async def tenant_a(pool) -> UUID:
    return await create_test_user(pool)


@pytest.fixture
async def tenant_b(pool) -> UUID:
    return await create_test_user(pool)


def _scope_ref(prefix: str = "SYM") -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


def _new_limit(
    *,
    tenant_id: UUID | None,
    scope: LimitScope = LimitScope.SYMBOL,
    scope_ref: str | None = None,
    metric: LimitMetric = LimitMetric.MAX_ORDER_NOTIONAL,
    limit_value: Decimal = Decimal("100"),
    updated_at: datetime | None = None,
) -> RiskLimit:
    return RiskLimit(
        id=uuid4(),
        tenant_id=tenant_id,
        scope=scope,
        scope_ref=scope_ref or _scope_ref(),
        metric=metric,
        limit_value=limit_value,
        updated_at=updated_at,
    )


# --- (1) 표현식 UNIQUE ---


async def test_expression_unique_blocks_duplicate_platform_default(pool):
    """tenant_id가 둘 다 NULL이면 일반 `UNIQUE(tenant_id, ...)`는 통과시키지만
    (Postgres `NULL <> NULL`), 표현식 UNIQUE는 `COALESCE`로 같은 값을 비교해
    두 번째 INSERT를 막는다."""
    scope_ref = _scope_ref()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
            "VALUES ($1, NULL, 'SYMBOL', $2, 'MAX_ORDER_NOTIONAL', 10)",
            uuid4(),
            scope_ref,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
                "VALUES ($1, NULL, 'SYMBOL', $2, 'MAX_ORDER_NOTIONAL', 20)",
                uuid4(),
                scope_ref,
            )


async def test_expression_unique_allows_same_scope_ref_for_different_tenants(
    pool, tenant_a, tenant_b
):
    """서로 다른 tenant_id는 `COALESCE`가 다른 값을 내므로 같은
    (scope, scope_ref, metric)이라도 각자 자기 행을 가질 수 있어야 한다
    (과잉 차단 방지 회귀)."""
    scope_ref = _scope_ref()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
            "VALUES ($1, $2, 'SYMBOL', $3, 'MAX_ORDER_NOTIONAL', 10)",
            uuid4(),
            tenant_a,
            scope_ref,
        )
        await conn.execute(
            "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
            "VALUES ($1, $2, 'SYMBOL', $3, 'MAX_ORDER_NOTIONAL', 10)",
            uuid4(),
            tenant_b,
            scope_ref,
        )


# --- (2) list_effective 교차 테넌트 0건 ---


async def test_list_effective_never_returns_other_tenants_rows(repo, tenant_a, tenant_b):
    symbol = _scope_ref()
    limit_a = _new_limit(tenant_id=tenant_a, scope=LimitScope.SYMBOL, scope_ref=symbol)
    limit_b = _new_limit(tenant_id=tenant_b, scope=LimitScope.SYMBOL, scope_ref=symbol)
    await repo.upsert(limit_a)
    await repo.upsert(limit_b)

    effective = await repo.list_effective(tenant_a, symbols=(symbol,))

    assert {row.id for row in effective} == {limit_a.id}
    assert all(row.tenant_id in (tenant_a, None) for row in effective)


async def test_list_effective_includes_platform_default(repo, tenant_a):
    symbol = _scope_ref()
    platform_default = _new_limit(tenant_id=None, scope=LimitScope.SYMBOL, scope_ref=symbol)
    await repo.upsert(platform_default)

    effective = await repo.list_effective(tenant_a, symbols=(symbol,))

    assert platform_default.id in {row.id for row in effective}


# --- (3) upsert 낙관적 잠금 ---


async def test_upsert_creates_new_row_when_no_conflict(repo, tenant_a):
    limit = _new_limit(tenant_id=tenant_a)
    saved = await repo.upsert(limit)
    assert saved.id == limit.id
    assert saved.updated_at is not None


async def test_upsert_with_correct_expected_updated_at_updates_row(repo, tenant_a):
    limit = _new_limit(tenant_id=tenant_a, limit_value=Decimal("10"))
    created = await repo.upsert(limit)

    changed = RiskLimit(
        id=created.id,
        tenant_id=created.tenant_id,
        scope=created.scope,
        scope_ref=created.scope_ref,
        metric=created.metric,
        limit_value=Decimal("25"),
        updated_at=created.updated_at,
    )
    updated = await repo.upsert(changed)
    assert updated.limit_value == Decimal("25")
    assert updated.updated_at != created.updated_at


async def test_upsert_with_stale_expected_updated_at_raises_and_leaves_row_unchanged(
    repo, tenant_a
):
    limit = _new_limit(tenant_id=tenant_a, limit_value=Decimal("10"))
    created = await repo.upsert(limit)

    stale = RiskLimit(
        id=created.id,
        tenant_id=created.tenant_id,
        scope=created.scope,
        scope_ref=created.scope_ref,
        metric=created.metric,
        limit_value=Decimal("999"),
        updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(ConcurrencyConflictError):
        await repo.upsert(stale)

    unchanged = await repo.list_effective(tenant_a, symbols=(created.scope_ref,))
    assert unchanged[0].limit_value == Decimal("10")


# --- (4) upsert_risk_limit 권한·tenant 스코프·감사 ---


async def _count_audit_rows(pool, aggregate_id: UUID) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM foundation_audit_event WHERE aggregate_id = $1",
            aggregate_id,
        )
    return int(row["n"])


async def test_upsert_risk_limit_rejects_non_operator_non_officer(repo, audit_repo, tenant_a):
    actor = LimitActor(subject_id=tenant_a, is_operator=False, is_risk_officer=False)
    limit = _new_limit(tenant_id=tenant_a)
    with pytest.raises(UnauthorizedLimitActorError):
        await upsert_risk_limit(
            repo, audit_repo, tenant_id=tenant_a, actor=actor, limit=limit
        )
    assert await _count_audit_rows(repo._pool, limit.id) == 0


async def test_upsert_risk_limit_rejects_risk_officer_outside_own_tenant(
    repo, audit_repo, tenant_a, tenant_b
):
    actor = LimitActor(subject_id=tenant_a, is_operator=False, is_risk_officer=True)
    other_tenants_limit = _new_limit(tenant_id=tenant_b)
    with pytest.raises(CrossTenantLimitScopeError):
        await upsert_risk_limit(
            repo, audit_repo, tenant_id=tenant_a, actor=actor, limit=other_tenants_limit
        )
    assert await _count_audit_rows(repo._pool, other_tenants_limit.id) == 0


async def test_upsert_risk_limit_rejects_risk_officer_setting_platform_default(
    repo, audit_repo, tenant_a
):
    actor = LimitActor(subject_id=tenant_a, is_operator=False, is_risk_officer=True)
    platform_default = _new_limit(tenant_id=None)
    with pytest.raises(CrossTenantLimitScopeError):
        await upsert_risk_limit(
            repo, audit_repo, tenant_id=tenant_a, actor=actor, limit=platform_default
        )


async def test_upsert_risk_limit_allows_risk_officer_within_own_tenant_and_audits_once(
    repo, audit_repo, tenant_a
):
    actor = LimitActor(subject_id=tenant_a, is_operator=False, is_risk_officer=True)
    limit = _new_limit(tenant_id=tenant_a)
    saved = await upsert_risk_limit(
        repo, audit_repo, tenant_id=tenant_a, actor=actor, limit=limit
    )
    assert saved.id == limit.id
    assert await _count_audit_rows(repo._pool, limit.id) == 1


async def test_upsert_risk_limit_allows_operator_to_set_platform_default(
    repo, audit_repo, tenant_a
):
    actor = LimitActor(subject_id=tenant_a, is_operator=True, is_risk_officer=False)
    platform_default = _new_limit(tenant_id=None)
    saved = await upsert_risk_limit(
        repo, audit_repo, tenant_id=tenant_a, actor=actor, limit=platform_default
    )
    assert saved.tenant_id is None
    assert await _count_audit_rows(repo._pool, platform_default.id) == 1


# --- (5) CHECK 거부 ---


async def test_negative_limit_value_rejected(pool, tenant_a):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
                "VALUES ($1, $2, 'SYMBOL', $3, 'MAX_ORDER_NOTIONAL', -1)",
                uuid4(),
                tenant_a,
                _scope_ref(),
            )


async def test_unknown_scope_rejected(pool, tenant_a):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
                "VALUES ($1, $2, 'BOGUS_SCOPE', $3, 'MAX_ORDER_NOTIONAL', 1)",
                uuid4(),
                tenant_a,
                _scope_ref(),
            )


async def test_unknown_metric_rejected(pool, tenant_a):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO risk_limit (id, tenant_id, scope, scope_ref, metric, limit_value) "
                "VALUES ($1, $2, 'SYMBOL', $3, 'BOGUS_METRIC', 1)",
                uuid4(),
                tenant_a,
                _scope_ref(),
            )


# --- record_breach ---


async def _insert_minimal_risk_decision(pool, *, tenant_id: UUID) -> UUID:
    decision_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_decision "
            "(decision_id, tenant_id, gate_kind, subject_fingerprint, outcome, "
            " rule_version, rule_hash, engine_version, inputs_hash, inputs_snapshot, "
            " trace_id, evaluated_at, expires_at) "
            "VALUES ($1, $2, 'PRE_TRADE', $3, 'DENY', 'v1', $4, 'engine-v1', $5, "
            " '{}'::jsonb, $6, now(), now())",
            decision_id,
            tenant_id,
            "f" * 64,
            "a" * 64,
            "b" * 64,
            uuid4(),
        )
    return decision_id


async def test_record_breach_inserts_row_linked_to_limit_and_decision(repo, pool, tenant_a):
    limit = _new_limit(tenant_id=tenant_a)
    saved = await repo.upsert(limit)
    decision_id = await _insert_minimal_risk_decision(pool, tenant_id=tenant_a)

    breach_id = await repo.record_breach(
        limit_id=saved.id,
        decision_id=decision_id,
        observed=Decimal("150"),
        limit_value=saved.limit_value,
        severity="CRITICAL",
        occurred_at=datetime.now(timezone.utc),
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT limit_id, decision_id, severity FROM risk_limit_breach WHERE id = $1",
            breach_id,
        )
    assert row["limit_id"] == saved.id
    assert row["decision_id"] == decision_id
    assert row["severity"] == "CRITICAL"
