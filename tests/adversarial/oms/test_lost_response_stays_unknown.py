"""task-2184 적대적 테스트 — 리뷰 task-2171 REJECT("응답 유실 주문이
FAILED로 확정된다")의 수정 증명.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.1 I10, §5.3
("전송 전 실패 = FAILED(reason), 전송 후 응답 유실 = UNKNOWN").

DoD가 요구하는 두 케이스를 절대 한 테스트로 묶지 않는다 — "전송했는가"가
실제 분기 기준임을 증명하려면 서로 다른 두 테스트가 서로 다른 결과를
내야 한다:
  (a) `test_response_lost_after_send_becomes_unknown` — 어댑터 호출 *이후*
      확인 불가한 예외(httpx.ReadTimeout) → status == 'UNKNOWN'.
  (b) `test_not_sent_failure_stays_failed` — 어댑터 호출에 도달했지만
      거래소에 전송되지 않았음이 확정된 예외(회로 OPEN) → status == 'FAILED'.
두 테스트 모두 `src.services.order_service.submit.submit_order`(legacy 동기
경로)를 거친다. `test_fenced_submit_response_lost_after_send_becomes_unknown`
은 같은 예외를 `fenced_submit.submit_with_fence`에 주입해 두 경로가 같은
결론을 내는지 대조한다(§C 중복 컨텍스트 금지 — 판정은 `dispatch_outcome.
classify_submit_failure` 하나뿐, 두 경로 모두 그것을 재사용한다).
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal

import asyncpg
import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.services.order_service.fenced_submit import submit_with_fence
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.submit import submit_order
from tests.adversarial.risk.conftest import (
    RecordingAdapter,
    fence_reader,
    insert_decision,
    make_order,
    order_row,
    seed_execution,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"lost-resp-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _market_order(execution_id: int) -> Order:
    return Order(
        client_order_id=f"lost-resp-{uuid.uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


async def test_response_lost_after_send_becomes_unknown(pool):
    """(a) 어댑터가 응답을 못 받은 것(httpx.ReadTimeout — 전송은 했을 수
    있으나 확인 불가)은 UNKNOWN이어야 한다. FAILED로 확정되면 이후 거래소
    확인이 와도 I4(터미널) 때문에 복구 불가 — 사용자가 재시도하면 중복
    주문이 된다(리뷰 task-2171 REJECT 근거)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)

    async def timed_out(order: Order) -> Order:
        raise httpx.ReadTimeout("timed out waiting for exchange response")

    adapter = FakeExchangeAdapter(on_place_order=timed_out)
    order = _market_order(execution_id)

    with pytest.raises(httpx.ReadTimeout):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert row is not None
    assert row["status"] == "UNKNOWN"


async def test_not_sent_failure_stays_failed(pool):
    """(b) 거래소에 전송되지 않았음이 확정된 실패(여기서는 회로 OPEN —
    어댑터가 네트워크를 열기도 전에 거부)는 여전히 FAILED여야 한다. (a)와
    반드시 다른 테스트여야 "전송했는가"가 실제 분기 기준임이 드러난다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)

    async def circuit_open(order: Order) -> Order:
        raise ExchangeError(
            ExchangeErrorKind.TRANSIENT_NETWORK, circuit_open=True, venue="bitget"
        )

    adapter = FakeExchangeAdapter(on_place_order=circuit_open)
    order = _market_order(execution_id)

    with pytest.raises(ExchangeError):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert row is not None
    assert row["status"] == "FAILED"


async def test_fenced_submit_response_lost_after_send_becomes_unknown(pool):
    """대조 테스트 — `fenced_submit.submit_with_fence`도 같은 예외에 같은
    결론(UNKNOWN)을 내야 한다. 두 경로 모두 `dispatch_outcome.
    classify_submit_failure`를 재사용하므로(재구현 금지) 판정이 갈릴 수
    없다는 것을 못 박는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    read = fence_reader(pool, user_id, execution_id)
    f0 = await read()
    gate = GateDecision(
        outcome=GateOutcome.ALLOW, fence_snapshot=f0, decision_id=decision.decision_id
    )

    async def timed_out(order: Order) -> Order:
        raise httpx.ReadTimeout("timed out waiting for exchange response")

    adapter = RecordingAdapter(on_place_order=timed_out)
    order = make_order(execution_id)

    with pytest.raises(httpx.ReadTimeout):
        await submit_with_fence(
            pool, adapter, order, user_id=user_id, gate_decision=gate, read_fences=read,
            decision_reader=PostgresDecisionRepository(pool),
        )

    row = await order_row(pool, order.order_id)
    assert row["status"] == "UNKNOWN"
