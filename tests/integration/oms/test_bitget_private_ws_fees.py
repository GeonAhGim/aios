"""L4-20 Bitget private WS fees integration coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.private_ws_mixin import (
    parse_private_order_row,
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.bitget_private_ws_support import (
    _envelope,
    _FakeConnection,
    _insert_order,
    _never,
    _no_rest_calls_adapter,
    _order_row,
    _StopTest,
    _TrackingConnectCtx,
)
from tests.integration.oms.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


@pytest.mark.perf
@pytest.mark.parametrize(
    "corrupt_fee",
    ["0", "-0.43", "-0.0042"],
    ids=[
        "negative-zero-fee",
        "negative-wrong-amount",
        "negative-wrong-scale",
    ],
)
async def test_negative_venue_fee_is_stored_as_positive_gate_red_regression(
    pool, monkeypatch, perf_budget, corrupt_fee
):
    """DEEPEN(2) — 명시적 게이트/CI 적색 회귀 테스트. Bitget은 체결 수수료를
    음수(`totalFee`)로 보낸다 — `_extract_fill`이 `abs()`로 정규화하지
    않으면(private_ws_mixin.py L99) 주문의 `fee_total`이 음수로 저장돼
    손익 계산이 조용히 틀어진다. 이 테스트는 그 `abs()` 호출 하나에
    묶여 있다: 누군가 그 호출을 지우면 아래 `fee_total > 0` 단언이 즉시
    적색이 되어 CI가 회귀를 잡는다.

    task-23040: fee parse best-of-5/batch=10000 실측 CPU 0.015625 ms,
    wall 0.015870 ms (Windows Python 3.13). 신규 1 ms/row 예산은
    CPU 실측의 64배 여유다. 동일 단언에 예산 10배 가상 지연을 주입한다.
    I-10 배선 증명: inbox 실패는 전파되고 복구 후 수수료가 정확히 저장된다.
    동일 이벤트 replay에도 수수료/체결 수가 불변.
    """
    user_id = await create_test_user(pool)
    exchange_order_id = f"ex-{uuid4().hex}"
    client_order_id = f"cid-{uuid4().hex}"
    order_id = await _insert_order(
        pool,
        user_id,
        quantity=Decimal("2"),
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
    )
    row = _order_row(order_id=exchange_order_id, client_id=client_order_id)
    row["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-0.42"}]  # 거래소는 음수로 보낸다
    message = _envelope([row])
    connection = _FakeConnection([message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)

    from tests.support.perf_inject import virtual_delayed_sync

    now = datetime.now(UTC)

    def parse_fee():
        return parse_private_order_row(row, received_at=now)

    def check_fee(event):
        assert event.last_fill is not None
        assert event.last_fill.fee == Decimal("0.42"), "fee normalization regression"

    check_fee(parse_fee())
    corrupted = dict(row, feeDetail=[{"feeCoin": "USDT", "totalFee": corrupt_fee}])
    with pytest.raises(AssertionError, match="fee normalization regression"):
        check_fee(parse_private_order_row(corrupted, received_at=now))

    budget_ms = 1.0

    def check_budget(fn):
        sample = perf_budget.best_of(fn, n=5, batch=10000)
        assert sample.cpu_ms < budget_ms, "fee parse budget exceeded"
        return sample

    check_fee(check_budget(parse_fee).result)
    with pytest.raises(AssertionError, match="fee parse budget exceeded"):
        check_budget(virtual_delayed_sync(parse_fee, budget_ms * 10 / 1000))

    async def fail_ingest(event):
        raise RuntimeError("injected fee inbox failure")

    failed_connection = _FakeConnection([message], hang=True)
    with monkeypatch.context() as patch:
        patch.setattr(inbox, "ingest", fail_ingest)
        with pytest.raises(RuntimeError, match="injected fee inbox failure"):
            await subscribe_bitget_orders_to_inbox(
                adapter,
                inbox,
                connect_fn=lambda url: _TrackingConnectCtx(failed_connection),
                ping_sleep_fn=_never,
            )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT fee_total FROM orders WHERE order_id = $1", order_id)
            is None
        )
        assert await conn.fetchval("SELECT count(*) FROM fills WHERE order_id = $1", order_id) == 0

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT fee_total, fee_currency FROM orders WHERE order_id = $1", order_id
        )
    assert order["fee_currency"] == "USDT"
    assert order["fee_total"] == Decimal("0.42")  # 부호가 뒤집혀 저장되지 않는다
    assert order["fee_total"] > 0

    await inbox.ingest(parse_fee())
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT fee_total FROM orders WHERE order_id = $1", order_id
        ) == Decimal("0.42")
        assert await conn.fetchval("SELECT count(*) FROM fills WHERE order_id = $1", order_id) == 1
