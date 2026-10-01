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
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    DecisionCorruptError,
    PostgresDecisionRepository,
)
from tests.integration.conftest import create_test_tenant

# `pool` 픽스처는 tests/adversarial/risk/conftest.py가 제공한다
# (os.environ["DATABASE_URL"] 사용 — tests/conftest.py가 TEST_DATABASE_URL을
# 여기로 옮겨 둔다). 이전에는 여기서 .env 파일을 직접 읽는 별도 픽스처를 뒀는데,
# 그러면 TEST_DATABASE_URL 오버라이드를 건너뛰고 개발 DB(aios_dev)에 그대로
# 연결돼 conftest.py가 막으려는 "테스트가 dev/prod DB에 연결"이 실제로
# 벌어졌다(QA에서 발견·수정).


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


def _decision(
    *, tenant_id: object, execution_ref: str = "exec:1", **overrides: Any
) -> RiskDecision:
    now = datetime.now(timezone.utc)
    base: dict[str, Any] = dict(
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
    return RiskDecision(**base)


async def _insert(
    repo: PostgresDecisionRepository, pool: asyncpg.Pool, *, tenant_id: object = None
) -> RiskDecision:
    tenant_id = tenant_id if tenant_id is not None else await create_test_tenant(pool)
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
    tenant_id = await create_test_tenant(pool)
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
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _insert(repo, pool, tenant_id=tenant_a)

    recent_b = await repo.list_recent(tenant_b, limit=50)

    assert recent_b == ()


async def test_list_recent_only_returns_own_tenant(
    pool: asyncpg.Pool, repo: PostgresDecisionRepository
) -> None:
    tenant_a = await create_test_tenant(pool)
    decision = await _insert(repo, pool, tenant_id=tenant_a)

    recent_a = await repo.list_recent(tenant_a, limit=50)

    assert decision.decision_id in [d.decision_id for d in recent_a]


async def test_get_propagates_injected_connection_failure_fail_closed() -> None:
    """실패 주입 -- 의존 asyncpg 커넥션이 `ConnectionDoesNotExistError`로 실패
    하면 `get()`은 이를 삼키거나 `None`으로 둔갑시키지 않고 그대로 전파해야
    한다(fail-closed, CLAUDE.md §3 105 표준). 실 Postgres로 커넥션 단절을
    결정적으로 재현할 수 없어(`tests/adversarial/ems/test_child_order_bypass.py`
    와 동일 근거) `pool`을 `AsyncMock`/`MagicMock`으로 격리했다."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = asyncpg.exceptions.ConnectionDoesNotExistError(
        "simulated connection drop"
    )
    mock_acquire_cm = AsyncMock()
    mock_acquire_cm.__aenter__.return_value = mock_conn
    mock_acquire_cm.__aexit__.return_value = False
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_acquire_cm

    repo = PostgresDecisionRepository(mock_pool)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.get(uuid4())
    mock_conn.fetchrow.assert_awaited_once()


async def test_get_fails_on_injected_corrupt_latency_us_row(pool: asyncpg.Pool) -> None:
    """게이트 적색 재현 -- `_row_to_decision`의 NULL `latency_us` fail-loud
    가드(task-2395, R-02 `RiskDecision.latency_us: int` 계약)가 실제로
    발동함을 보인다. 정상 쓰기 경로(`insert()`)는 pydantic이 NULL을 구성
    단계에서부터 거부해 이 행을 만들 수 없으므로,
    `tests/integration/risk/test_risk_limits_db.py::_insert_minimal_risk_decision`
    와 동일한 패턴으로 계약을 우회하는 raw SQL INSERT로 오염된 행을 직접
    심는다(`latency_us`만 NULL로 남긴다). 이 가드가 없다면 `get()`은
    `pydantic.ValidationError`로 호출자를 깨뜨렸을 것이다(docstring 17-30행)."""
    tenant_id = await create_test_tenant(pool)
    decision_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_decision "
            "(decision_id, tenant_id, gate_kind, subject_fingerprint, outcome, "
            " rule_version, rule_hash, engine_version, inputs_hash, inputs_snapshot, "
            " trace_id, evaluated_at, expires_at, latency_us) "
            "VALUES ($1, $2, 'PRE_TRADE', $3, 'DENY', 'v1', $4, 'engine-v1', $5, "
            " '{}'::jsonb, $6, now(), now(), NULL)",
            decision_id,
            tenant_id,
            "f" * 64,
            "b" * 64,
            "c" * 64,
            uuid4(),
        )

    repo = PostgresDecisionRepository(pool)
    with pytest.raises(DecisionCorruptError, match="latency_us"):
        await repo.get(decision_id)
