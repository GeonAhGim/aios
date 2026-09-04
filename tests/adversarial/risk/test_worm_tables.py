"""R-24 적대적 — `risk_decision` WORM 강제 + 교차 tenant 격리.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-24, §4.1 I7·I12/R12.

`tests/integration/test_db_roles.py`의 `_assert_append_only_violation`
패턴(REVOKE·트리거 두 방어층 중 어느 쪽이 먼저 발동하는지는 계약이 아니다)과
`tests/adversarial/ledger/test_role_bypass.py`류의 "테이블 소유자로 직접
UPDATE해 트리거 자체가 살아있음을 증명" 재현을 `risk_decision`에 그대로
적용한다 — task-1210 decision에 따라 새 WORM 방식을 만들지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


def _decision(
    *, tenant_id: object, execution_ref: str = "exec:1", **overrides: object
) -> RiskDecision:
    now = datetime.now(timezone.utc)
    base: dict[str, object] = dict(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_TRADE,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        subject_fingerprint="a" * 64,
        outcome=RiskOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        rule_results=(),
        rule_version="2026.09.1",
        rule_hash="b" * 64,
        engine_version="risk-engine/2",
        inputs_hash="c" * 64,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(minutes=5),
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=100,
    )
    base.update(overrides)
    return RiskDecision(**base)  # type: ignore[arg-type]


async def _insert(
    repo: PostgresDecisionRepository, pool: asyncpg.Pool, *, tenant_id: object = None
) -> RiskDecision:
    tenant_id = tenant_id if tenant_id is not None else await create_test_user(pool)
    decision = _decision(tenant_id=tenant_id)
    await repo.insert(decision, {"balance": "10000"})
    return decision


def _assert_append_only_violation(exc_info: pytest.ExceptionInfo) -> None:
    """WORM 방어는 REVOKE·트리거 두 층이고 어느 쪽이 먼저 발동하는지는 계약이
    아니다(`tests/integration/test_db_roles.py`와 동일 근거) — 트리거가 실제로
    발동한 경우(RaiseError)에 한해 메시지가 WORM 가드인지 확인한다."""
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


async def test_naive_evaluated_at_rejected_before_insert(pool: asyncpg.Pool) -> None:
    """`RiskDecision`(R-02)이 naive datetime을 거부하므로, 이 저장소로는
    naive datetime을 가진 결정을 애초에 만들 수조차 없다 — DB에 도달하기 전에
    막힌다."""
    tenant_id = await create_test_user(pool)
    with pytest.raises(ValidationError):
        _decision(tenant_id=tenant_id, evaluated_at=datetime(2026, 9, 4))


async def test_insert_then_get_round_trip(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    decision = await _insert(repo, pool)

    got = await repo.get(decision.decision_id)

    assert got is not None
    fetched, snapshot = got
    assert fetched.decision_id == decision.decision_id
    assert fetched.tenant_id == decision.tenant_id
    assert fetched.outcome == RiskOutcome.ALLOW
    assert fetched.evidence_ref is None
    assert snapshot == {"balance": "10000"}


async def test_get_missing_decision_returns_none(repo: PostgresDecisionRepository) -> None:
    assert await repo.get(uuid4()) is None


async def test_aios_app_cannot_update_risk_decision(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    decision = await _insert(repo, pool)

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE risk_decision SET outcome = 'DENY' WHERE decision_id = $1",
                decision.decision_id,
            )
    _assert_append_only_violation(exc_info)


async def test_aios_app_cannot_delete_risk_decision(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    decision = await _insert(repo, pool)

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "DELETE FROM risk_decision WHERE decision_id = $1", decision.decision_id
            )
    _assert_append_only_violation(exc_info)


async def test_worm_trigger_blocks_table_owner_update(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    """REVOKE는 테이블 소유자에게 적용되지 않는다(PostgreSQL 원칙) — `pool`은
    `SET ROLE` 없이 접속하며 `risk_decision`을 실제로 소유하고 있으므로(마이그
    레이션을 이 계정으로 실행했다), 여기서 UPDATE가 막힌다면 REVOKE와 무관하게
    트리거 자체가 살아 있다는 뜻이다. Spec: I7 '소유자도 우회 불가'."""
    decision = await _insert(repo, pool)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE risk_decision SET outcome = 'DENY' WHERE decision_id = $1",
                decision.decision_id,
            )


async def test_worm_trigger_blocks_table_owner_delete(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    decision = await _insert(repo, pool)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM risk_decision WHERE decision_id = $1", decision.decision_id
            )


async def test_list_recent_excludes_other_tenant(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await _insert(repo, pool, tenant_id=tenant_a)

    recent_b = await repo.list_recent(tenant_b, limit=50)

    assert recent_b == ()


async def test_list_recent_only_returns_own_tenant(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    tenant_a = await create_test_user(pool)
    decision = await _insert(repo, pool, tenant_id=tenant_a)

    recent_a = await repo.list_recent(tenant_a, limit=50)

    assert decision.decision_id in [d.decision_id for d in recent_a]
