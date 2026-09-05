"""`tests/adversarial/risk/` 공용 픽스처·헬퍼(R-37 fence 경합·트리거 테스트).

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL`로 옮겨 두므로
여기서는 asyncpg DSN 변환과, `orders`의 FK 대상(`users`, `strategy_executions`,
`risk_decision`)을 만드는 최소 헬퍼만 둔다. 다른 adversarial 디렉터리
(`execution_ownership/conftest.py`)와 같은 관례.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter, PlaceOrderHook


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=12)
    yield p
    await p.close()


class SpyMetrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        pass

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        pass


class RecordingAdapter(FakeExchangeAdapter):
    """cancel 호출을 기록하는 fake — post-fence 되돌리기 증명용."""

    def __init__(self, *, on_place_order: PlaceOrderHook | None = None) -> None:
        super().__init__(exchange_name="bitget", on_place_order=on_place_order)
        self.cancelled_exchange_order_ids: list[str] = []

    async def cancel_order(self, order_id: str) -> bool:
        self.cancelled_exchange_order_ids.append(order_id)
        return True


async def seed_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str = "bitget") -> int:
    strategy_id = f"fence-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
        )
    return row["id"]


async def recorded_inputs(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    execution_ref: str,
    exchange: str = "bitget",
    symbol: str = "BTC/USDT",
    side: str = "BUY",
    quantity: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """R-35 `_PreSubmitInputs.model_dump(mode="json")`과 같은 형태의 WORM
    `inputs_snapshot` — fence는 지금 DB 값(`fence_pairs_for` 5쌍)을 읽어
    넣는다. 기본 intent는 `make_order` 기본값과 일치한다(대조군이 통과해야
    negative 테스트가 의미를 가진다)."""
    repo = PostgresRiskGateRepository(pool)
    snapshot = await repo.read_fences(fence_pairs_for(tenant_id, exchange, execution_ref))
    return {
        "schema_version": "v1",
        "tenant_id": str(tenant_id),
        "execution_ref": execution_ref,
        "provider_code": exchange,
        "symbol": symbol,
        "side": side,
        "quantity": str(quantity),
        "fence_snapshot": {
            f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()
        },
    }


async def insert_decision(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    outcome: RiskOutcome = RiskOutcome.ALLOW,
    ttl: timedelta = timedelta(minutes=5),
    execution_ref: str = "exec:1",
    inputs_snapshot: dict[str, Any] | None = None,
) -> RiskDecision:
    """`inputs_snapshot=None`이면 `recorded_inputs()`(정직한 스냅샷). negative
    테스트는 결손·변조된 스냅샷을 직접 넘긴다."""
    if inputs_snapshot is None:
        inputs_snapshot = await recorded_inputs(pool, tenant_id, execution_ref=execution_ref)
    now = datetime.now(timezone.utc)
    decision = RiskDecision(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_SUBMIT,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        subject_fingerprint="a" * 64,
        outcome=outcome,
        reason_codes=(),
        obligations=(),
        rule_results=(),
        rule_version="2026.09.1",
        rule_hash="b" * 64,
        engine_version="risk-engine/2",
        inputs_hash="c" * 64,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + ttl,
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=10,
    )
    await PostgresDecisionRepository(pool).insert(decision, inputs_snapshot)
    return decision


def make_order(execution_id: int, *, exchange: str = "bitget") -> Order:
    return Order(
        client_order_id=f"fence-{uuid.uuid4().hex}",
        strategy_id="fence-race",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange=exchange,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


def fence_reader(pool: asyncpg.Pool, user_id: UUID, execution_id: int, *, exchange: str = "bitget"):
    """`foundation_gate._flatten_fence`와 같은 형식(`"SCOPE:ref" -> token`)."""
    repo = PostgresRiskGateRepository(pool)
    pairs = fence_pairs_for(user_id, exchange, f"exec:{execution_id}")

    async def read() -> Mapping[str, int]:
        snapshot = await repo.read_fences(pairs)
        return {f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()}

    return read


async def order_row(pool: asyncpg.Pool, order_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, risk_decision_id FROM orders WHERE order_id = $1", order_id
        )
    assert row is not None
    return row


async def audit_count(pool: asyncpg.Pool, action_type: str, order_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE action_type = $1 AND target_id = $2",
            action_type,
            str(order_id),
        )
