"""FA-5 DoD(1) — `submit_order`가 `entity_context` 없이는 어떤 저장소 write도
시도하지 않는다는 negative test. 실 DB 없이(페이크 pool) — 가드가 어댑터
INSERT보다 먼저 걸린다는 것을 `pool.acquire()` 호출 횟수(카운터)로 단언한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5 §9 DoD
"fund/portfolio 컨텍스트 없이 submit_order를 호출하면 도메인 예외로 거부되는
negative test 1건(어댑터 INSERT 호출 0회를 카운터로 단언)".
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.application.resolve_context import EntityContextResolutionError
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import IdempotencyScope, SubmitOrderCommand
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext


def _command() -> SubmitOrderCommand:
    user_id = uuid4()
    scope = IdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=1, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid4(), trace_id=uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


class _PoolSpy:
    """`asyncpg.Pool`의 최소 대역 — `acquire()`가 한 번이라도 불리면 orders
    INSERT를 시도했다는 뜻이므로 즉시 실패시킨다. 카운터는 그 전에
    entity_context 가드가 막았음을 단언하는 데 쓴다(DoD "INSERT 호출 0회")."""

    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self):  # noqa: ANN201 - 테스트 대역, 호출되면 즉시 실패해야 한다
        self.acquire_calls += 1
        raise AssertionError(
            "entity_context 가드보다 먼저 pool.acquire()가 호출됐습니다 — "
            "orders INSERT 시도는 fail-closed 가드 이후에만 일어나야 합니다."
        )


async def test_submit_order_rejects_missing_entity_context_without_touching_adapter():
    pool = _PoolSpy()

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            _command(),
            pool=pool,  # type: ignore[arg-type]
            profile=None,  # type: ignore[arg-type]
            registry=None,  # type: ignore[arg-type]
            pre_submit_gate=_allow_gate,
            entity_context=None,  # type: ignore[arg-type]
        )

    assert pool.acquire_calls == 0
