"""L4-20 Bitget private WS parsing, inbox routing and rejection coverage."""

from __future__ import annotations

import asyncio
import contextlib
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
    _instant,
    _never,
    _no_rest_calls_adapter,
    _order_row,
    _StopTest,
    _TrackingConnectCtx,
)
from tests.integration.oms.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders

_MISSING_ID_PARSE_BUDGET_MS = 50.0
_FILL_FLOW_BUDGET_MS = 500.0


async def test_fill_event_reaches_inbox_and_marks_order_filled_idempotently(pool):
    """DoD(a) — 체결 이벤트 1건이 inbox를 거쳐 FILLED로 전이하고 포지션
    (여기서는 fills/orders 행)이 정확히 1회만 반영된다. 같은 이벤트를
    2회(별도 WS 메시지로) 주입해도 fills 행 수가 늘지 않음을 수치로
    단언한다(멱등, I-03)."""
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
    message = _envelope([row])
    connection = _FakeConnection([message, message], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert url == "wss://ws.bitget.com/v2/ws/private"
            return _TrackingConnectCtx(connection)
        raise _StopTest

    adapter = _no_rest_calls_adapter()
    inbox = InboxProcessor(pool)

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == Decimal("2")
    assert fills_count == 1  # 중복 전달 2회 → fills 여전히 1행(멱등)


async def test_event_missing_order_identifiers_is_not_silently_dropped(pool):
    """DoD(b)/1 — 주문 ID(orderId/clientOid)가 전혀 없는 이벤트도 조용히
    버리지 않는다: 드롭 카운트가 아니라 inbox 행(IGNORED) 생성을
    단언한다."""
    row = {
        "instId": "BTCUSDT",
        "status": "live",
        # orderId/clientOid/tradeId 전부 없음 — 체결 수량도 없음. nonce는
        # raw_hash(→provider_event_id)를 실행마다 고유하게 만들어, 초기화
        # 안 되는 TEST_DATABASE_URL에 남은 이전 실행분과 충돌하지 않게 한다.
        "_test_nonce": uuid4().hex,
    }
    expected_provider_event_id = parse_private_order_row(
        row, received_at=datetime.now(UTC)
    ).provider_event_id
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

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            expected_provider_event_id,
        )
    assert state == "IGNORED"  # 드롭이 아니라 inbox 행 생성(fail-closed)


@pytest.mark.parametrize("blank", [None, "", 0])
def test_missing_identifier_blank_values_yield_unresolved_event_not_exception(blank):
    """DEEPEN-DEPTH negative 1 -- orderId/clientOid present-but-blank
    (None / "" / 0) must be treated like absent: no exception, no ids, and
    the auditable `unresolved` provider_event_id."""
    row = {"instId": "BTCUSDT", "status": "live", "orderId": blank, "clientOid": blank}
    ev = parse_private_order_row(row, received_at=datetime.now(UTC))
    assert ev.exchange_order_id is None
    assert ev.client_order_id is None
    assert ev.last_fill is None
    assert ev.filled_quantity == Decimal("0")
    assert ev.provider_event_id == f"bitget:orders:unresolved:{ev.raw_hash}"


def test_missing_identifier_events_with_different_payloads_get_distinct_ids():
    """DEEPEN-DEPTH negative 2 -- two distinct id-less events must not
    collapse into one inbox key (that would be a silent drop by dedup),
    while a byte-identical redelivery must map to the same key."""
    now = datetime.now(UTC)
    a = {"instId": "BTCUSDT", "status": "live"}
    b = {"instId": "BTCUSDT", "status": "cancelled"}
    ev_a1 = parse_private_order_row(a, received_at=now)
    ev_a2 = parse_private_order_row(dict(a), received_at=now)
    ev_b = parse_private_order_row(b, received_at=now)
    assert ev_a1.provider_event_id == ev_a2.provider_event_id
    assert ev_a1.provider_event_id != ev_b.provider_event_id


async def test_missing_identifier_non_dict_rows_are_skipped_but_valid_rows_still_land(pool):
    """DEEPEN-DEPTH negative 3 / failure injection -- a malformed envelope
    (non-dict rows mixed in) must not abort the stream: the garbage rows are
    skipped and the id-less dict row still produces an IGNORED inbox row."""
    row = {"instId": "ETHUSDT", "status": "live", "_test_nonce": uuid4().hex}
    expected = parse_private_order_row(row, received_at=datetime.now(UTC)).provider_event_id
    message = _envelope(["garbage", 7, None, row])
    connection = _FakeConnection([message], raise_after=ConnectionClosed(None, None))
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=connect_fn,
            ping_sleep_fn=_never,
        )
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1", expected
        )
    assert state == "IGNORED"


async def test_missing_identifier_ingest_failure_propagates_not_swallowed(pool):
    """DEEPEN-DEPTH failure injection -- if the inbox write itself fails the
    error must surface (fail-closed), never be swallowed as a silent drop."""

    class _BoomInbox(InboxProcessor):
        def __init__(self) -> None:
            pass

        async def ingest(self, ev):
            raise RuntimeError("injected inbox failure")

    row = {"instId": "BTCUSDT", "status": "live", "_test_nonce": uuid4().hex}
    connection = _FakeConnection([_envelope([row])], hang=True)

    with pytest.raises(RuntimeError, match="injected inbox failure"):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            _BoomInbox(),
            connect_fn=lambda url: _TrackingConnectCtx(connection),
            ping_sleep_fn=_never,
        )


def _parse_missing_id_batch() -> None:
    """200 id-less parses. Local measurement (best-of-5 cpu): ~1-2 ms, so
    the 50 ms budget leaves >=25x headroom (rounded to the 30x rule by the
    10x virtual-delay red check below)."""
    now = datetime.now(UTC)
    for i in range(200):
        parse_private_order_row({"instId": "BTCUSDT", "status": "live", "seq": i}, received_at=now)


@pytest.mark.perf
def test_missing_identifier_parse_within_budget(perf_budget):
    """DEEPEN-DEPTH perf -- best-of-N cpu budget for the id-less parse path."""
    perf_budget.assert_within(
        _parse_missing_id_batch,
        budget_ms=_MISSING_ID_PARSE_BUDGET_MS,
        n=5,
        label="missing_id_parse",
    )


@pytest.mark.perf
def test_missing_identifier_parse_budget_goes_red_under_10x_virtual_delay(perf_budget):
    """DEEPEN-DEPTH gate-red repro -- inject a virtual delay of 10x the
    budget; the same assertion must fail, proving it actually catches
    slowdowns."""
    from tests.support.perf_inject import virtual_delayed_sync

    slow = virtual_delayed_sync(_parse_missing_id_batch, _MISSING_ID_PARSE_BUDGET_MS * 10 / 1000)
    with pytest.raises(AssertionError, match="missing_id_parse"):
        perf_budget.assert_within(
            slow,
            budget_ms=_MISSING_ID_PARSE_BUDGET_MS,
            n=3,
            label="missing_id_parse",
        )


async def test_event_pointing_at_unknown_order_id_is_not_silently_dropped(pool):
    """DoD(b)/2 — 알 수 없는 주문 ID를 가리키는 이벤트도 inbox 행을
    남긴다(IGNORED, L4-15의 fail-closed 매칭 경로를 그대로 탄다)."""
    exchange_order_id = f"ex-does-not-exist-{uuid4().hex}"
    client_order_id = f"cid-does-not-exist-{uuid4().hex}"
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(order_id=exchange_order_id, client_id=client_order_id, trade_id=trade_id)
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

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE exchange_order_id = $1", exchange_order_id
        )
    assert state == "IGNORED"
    assert fills_count == 0


async def test_fill_event_for_terminal_order_does_not_resurrect_it(pool):
    """DEEPEN(3) — true negative/rejection 테스트 3번째(기존 2개: 식별자
    누락/미지 주문). 이미 종결(CANCELLED)된 주문에 뒤늦게 도착한 체결
    이벤트는 조용히 버려지지도, 종결 상태를 뒤엎지도 않는다 —
    `InboxProcessor._process_row`의 `is_terminal(order.status)` fail-closed
    분기(inbox_processor.py L192-194, "이미 종결 — 늦은 중복 전달")가 이
    WS 배선 경로를 통해서도 실제로 지켜짐을 증명한다. inbox 행은
    PROCESSED로 남는다(드롭이 아니라 처리 완료로 기록, I-04 종결 불변)."""
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
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orders SET status = 'CANCELLED' WHERE order_id = $1", order_id)
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(order_id=exchange_order_id, client_id=client_order_id, trade_id=trade_id)
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

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            adapter, inbox, connect_fn=connect_fn, ping_sleep_fn=_never
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
    assert order["status"] == "CANCELLED"  # 종결 상태가 뒤집히지 않았다
    assert order["filled_quantity"] == Decimal("0")
    assert fills_count == 0
    assert state == "PROCESSED"  # 드롭이 아니라 "늦은 중복"으로 처리 완료


async def test_fill_ingest_failure_propagates_and_leaves_order_untouched(pool):
    """DEEPEN-DEPTH failure injection for the fill->FILLED path -- an inbox
    write failure must surface (fail-closed) and must not leave the order
    partially transitioned or any fill row behind."""

    class _BoomInbox(InboxProcessor):
        def __init__(self) -> None:
            pass

        async def ingest(self, ev):
            raise RuntimeError("injected inbox failure")

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
    message = _envelope([_order_row(order_id=exchange_order_id, client_id=client_order_id)])
    connection = _FakeConnection([message], hang=True)

    with pytest.raises(RuntimeError, match="injected inbox failure"):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            _BoomInbox(),
            connect_fn=lambda url: _TrackingConnectCtx(connection),
            ping_sleep_fn=_never,
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    assert order["status"] == "SUBMITTED"
    assert order["filled_quantity"] == Decimal("0")
    assert fills_count == 0


async def test_fill_assertions_go_red_when_event_targets_wrong_order(pool):
    """DEEPEN-DEPTH gate-red repro -- corrupt the input (event carries a
    different exchange order id) and show the FILLED / fills==1 assertions of
    the end-to-end test really fail, i.e. they are not vacuous."""
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
    broken = _order_row(order_id=f"ex-{uuid4().hex}", client_id=f"cid-{uuid4().hex}")
    connection = _FakeConnection([_envelope([broken])], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=connect_fn,
            sleep_fn=_instant,
            ping_sleep_fn=_never,
        )

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    with pytest.raises(AssertionError):
        assert order["status"] == "FILLED"
    with pytest.raises(AssertionError):
        assert fills_count == 1


async def _run_fill_flow(pool) -> None:
    """One fresh order + one FILL event through WS -> inbox -> FILLED."""
    user_id = await create_test_user(pool)
    ex_id, cid = f"ex-{uuid4().hex}", f"cid-{uuid4().hex}"
    order_id = await _insert_order(
        pool, user_id, quantity=Decimal("2"), exchange_order_id=ex_id, client_order_id=cid
    )
    message = _envelope([_order_row(order_id=ex_id, client_id=cid)])
    connection = _FakeConnection([message], hang=True)
    task = asyncio.create_task(
        subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=lambda url: _TrackingConnectCtx(connection),
            ping_sleep_fn=_never,
        )
    )
    status = "SUBMITTED"
    for _ in range(200):
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
        if status == "FILLED":
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert status == "FILLED"


@pytest.mark.perf
async def test_fill_event_flow_within_budget(pool, perf_budget):
    """Perf -- best-of-N wall budget for the fill->inbox->FILLED flow. Local
    best-of-3 measurement: ~15 ms wall (2026-11-12); 500 ms budget = ~33x headroom."""
    sample = await perf_budget.best_of_async(lambda: _run_fill_flow(pool), n=3, warmup=1)
    print(f"fill_flow wall_ms={sample.wall_ms:.3f}")
    assert sample.wall_ms < _FILL_FLOW_BUDGET_MS, f"fill_flow wall={sample.wall_ms:.3f}ms"


@pytest.mark.perf
async def test_fill_event_flow_budget_goes_red_under_10x_virtual_delay(pool, perf_budget):
    """Gate-red repro -- 10x virtual delay must trip the same assertion."""
    from tests.support.perf_inject import virtual_delayed_async

    slow = virtual_delayed_async(lambda: _run_fill_flow(pool), _FILL_FLOW_BUDGET_MS * 10 / 1000)
    sample = await perf_budget.best_of_async(slow, n=2)
    with pytest.raises(AssertionError, match="fill_flow"):
        assert sample.wall_ms < _FILL_FLOW_BUDGET_MS, f"fill_flow wall={sample.wall_ms:.3f}ms"
