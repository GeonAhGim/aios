"""R-37 orders_require_risk_decision 트리거 — 무장·유효성·청산.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-37 DoD("트리거 거부"), §4.1 I1.

트리거 orders_require_risk_decision(BEFORE INSERT OR UPDATE)의 규칙:
1. liquidation: is_liquidation=TRUE면 liquidation_request_id 필수.
2. decision validity: risk_decision_id가 있으면 (a) 같은 tenant, (b) ALLOW/REDUCE outcome,
   (c) 만료 전이어야 한다. 즉시 강제, cutover 무관.
3. cutover arming: cutover_at이 설정되고 created_at >= cutover_at이면
   risk_decision_id 필수.

DB 제약이므로 asyncpg.CheckViolationError/ForeignKeyViolationError로 낙아진다.
트랜잭션 안에서 cutover를 무장한 뒤 롤백해 운영 DB에 영향 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from tests.adversarial.risk.conftest import (
    insert_decision,
    seed_execution,
)
from tests.integration.conftest import create_test_tenant

pool = None  # 픽스처 re-export(execution_ownership conftest 관례)


@pytest.fixture
async def ctx(pool: asyncpg.Pool) -> dict[str, Any]:
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    return {"pool": pool, "user_id": user_id, "execution_id": execution_id, "decision": decision}


async def _insert_raw(
    conn: asyncpg.Connection,
    user_id: UUID,
    execution_id: int,
    *,
    risk_decision_id: UUID | None = None,
    is_liquidation: bool = False,
    liquidation_request_id: UUID | None = None,
    created_at: datetime | None = None,
) -> UUID:
    result = await conn.fetchval(
        """
        INSERT INTO orders (
            user_id, client_order_id, strategy_id, strategy_version, execution_id, symbol,
            exchange, side, order_type, quantity, status, is_liquidation, asset_class,
            risk_decision_id, liquidation_request_id, created_at
        ) VALUES ($1, $2, 'trigger-test', '1.0.0', $3, 'BTC/USDT', 'bitget', 'BUY', 'MARKET',
                  0.01, 'CREATED', $4, 'CRYPTO', $5, $6, COALESCE($7, now()))
        RETURNING order_id
        """,
        user_id,
        f"raw-{uuid4().hex}",
        execution_id,
        is_liquidation,
        risk_decision_id,
        liquidation_request_id,
        created_at,
    )
    return result if isinstance(result, UUID) else uuid4()


async def test_trigger_rejects_order_without_decision_once_cutover_armed(
    pool: asyncpg.Pool, ctx: dict[str, Any]
) -> None:
    """cutover 무장 후 결정 없는 INSERT 거부."""
    arm_sql = (
        "UPDATE orders_risk_decision_cutover SET cutover_at = now(), armed_by = 'test' "
        "WHERE id = 1 AND cutover_at IS NULL RETURNING cutover_at"
    )
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _insert_raw(conn, ctx["user_id"], ctx["execution_id"])  # 비무장(현재 운영 상태)
            armed_at = await conn.fetchval(arm_sql)
            assert armed_at is not None
            assert await conn.fetchval(arm_sql) is None  # 단조: 재무장은 no-op

            with pytest.raises(asyncpg.CheckViolationError, match="has no risk_decision_id"):
                async with conn.transaction():
                    await _insert_raw(conn, ctx["user_id"], ctx["execution_id"])
            with_decision = await _insert_raw(
                conn,
                ctx["user_id"],
                ctx["execution_id"],
                risk_decision_id=ctx["decision"].decision_id,
            )
            await _insert_raw(
                conn,
                ctx["user_id"],
                ctx["execution_id"],
                created_at=armed_at - timedelta(seconds=1),  # 이전 행
            )
            with pytest.raises(asyncpg.CheckViolationError):  # UPDATE로 참조 제거 우회 불가
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET risk_decision_id = NULL WHERE order_id = $1",
                        with_decision,
                    )
        finally:
            await tr.rollback()


@pytest.mark.parametrize("case", ["other_tenant", "deny", "expired", "unknown", "other_execution"])
async def test_trigger_rejects_invalid_decision_reference_even_when_disarmed(
    pool: asyncpg.Pool, ctx: dict[str, Any], case: str
) -> None:
    """결정 유효성(tenant·outcome·만료): cutover 무관하게 즉시 강제."""
    if case == "other_execution":  # 같은 tenant·ALLOW·유효지만 다른 execution
        ref = f"exec:{await seed_execution(pool, ctx['user_id'])}"
        other = await insert_decision(pool, ctx["user_id"], execution_ref=ref)
        decision_id = other.decision_id
    elif case == "other_tenant":
        decision_id = (await insert_decision(pool, await create_test_tenant(pool))).decision_id
    elif case == "deny":
        deny = await insert_decision(pool, ctx["user_id"], outcome=RiskOutcome.DENY)
        decision_id = deny.decision_id
    elif case == "expired":
        expired = await insert_decision(pool, ctx["user_id"], ttl=timedelta(seconds=-1))
        decision_id = expired.decision_id
    else:
        decision_id = uuid4()
    async with pool.acquire() as conn:
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            await _insert_raw(
                conn, ctx["user_id"], ctx["execution_id"], risk_decision_id=decision_id
            )
        assert await _insert_raw(
            conn, ctx["user_id"], ctx["execution_id"], risk_decision_id=ctx["decision"].decision_id
        )  # 대조군


async def test_trigger_liquidation_requires_request_id(
    pool: asyncpg.Pool, ctx: dict[str, Any]
) -> None:
    """청산 주문은 liquidation_request_id 필수."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError, match="requires liquidation_request_id"):
            await _insert_raw(conn, ctx["user_id"], ctx["execution_id"], is_liquidation=True)
        assert await _insert_raw(
            conn,
            ctx["user_id"],
            ctx["execution_id"],
            is_liquidation=True,
            liquidation_request_id=uuid4(),
        )
