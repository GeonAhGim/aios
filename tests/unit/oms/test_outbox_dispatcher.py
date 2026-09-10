"""L4-14 outbox 디스패처 단위 테스트(포트 대역, §8.2 `test_outbox_dispatcher.py` 표).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §8.2, §9 L4-14 DoD.

케이스: PENDING→DONE(SUBMITTED→ACK); `SentUnknownError` → UNKNOWN & 재전송 없음
(어댑터 호출 1회); 회로 OPEN → not_before 연기(attempt 불변); 재시도 상한 → DEAD;
게이트 거부 시 거래소 미호출(I-01/I-10); payload 불일치 fail-closed; 전송 전
DEAD(어댑터 호출 0회) → `FAILED(SEND_ABANDONED)`, 전송 후 DEAD → `UNKNOWN` 유지
(CA 2026-09-06); F14 채택; CANCEL/MODIFY; run_forever 생존; wiring이 foundation
게이트를 쓰는지(I-10).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    SentUnknownError,
)
from src.services.oms.application import wiring
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    classify_idempotent_failure,
    classify_submit_failure,
)
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from tests.support.oms_outbox_fakes import (
    FakePool,
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    allow_gate,
    deny_gate,
    enqueue,
    gate_param_has_no_default,
    make_dispatcher,
    make_order_view,
    submit_payload,
)


def _raising(exc: BaseException) -> Any:
    async def hook(order: Order) -> Order:
        raise exc

    return hook


async def _run(outbox, orders, adapter, clock, **kwargs: Any):
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock, **kwargs)
    return await d.dispatch_once()


def _rejecting() -> Any:
    async def hook(order: Order) -> Order:
        return order.model_copy(update={"status": OrderStatus.REJECTED})

    return hook


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def outbox(clock: FixedClock) -> InMemoryOutboxRepo:
    return InMemoryOutboxRepo(clock=clock)


@pytest.fixture
def orders() -> InMemoryOrderRepo:
    return InMemoryOrderRepo()


# ---- SUBMIT 정상/UNKNOWN ------------------------------------------------------------
async def test_pending_to_done_submitted_to_ack(outbox, orders, clock):
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock)

    assert (report.claimed, report.acknowledged) == (1, 1)
    assert outbox.rows[row_id].state == "DONE"
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.ACKNOWLEDGED
    assert final.exchange_order_id and final.version == 3  # SENT(+1) → ACK(+1)
    assert [e.event for e in orders.events] == ["SENT", "ACK"]
    assert orders.events[0].to_status is OrderStatus.SUBMITTED
    assert adapter.calls == [view.client_order_id]


async def test_sent_unknown_marks_unknown_once_and_never_resends(outbox, orders, clock):
    """DoD — SentUnknown → UNKNOWN, 어댑터 호출 정확히 1회, outbox DONE(F3)."""
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter(on_place=_raising(SentUnknownError(venue="bitget")))
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)

    first = await d.dispatch_once()
    assert (first.unknown, first.retried) == (1, 0)
    assert outbox.rows[row_id].state == "DONE"
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.UNKNOWN and final.unknown_since == clock.now
    assert orders.events[-1].event == "RESPONSE_LOST"
    assert orders.events[-1].reason_code == "SENT_UNKNOWN"

    clock.advance(3600)
    second = await d.dispatch_once()
    assert second.claimed == 0
    assert adapter.calls == [view.client_order_id]


async def test_unclassified_exception_after_send_is_unknown(outbox, orders, clock):
    """레거시 RetryableExchangeError/RuntimeError — 전송 여부를 모르면 UNKNOWN(I10)."""
    for exc in (RetryableExchangeError("boom"), RuntimeError("socket reset")):
        view = orders.add(make_order_view())
        await enqueue(outbox, view)
        adapter = ScriptedAdapter(on_place=_raising(exc))
        report = await _run(outbox, orders, adapter, clock)
        assert report.unknown == 1 and report.retried == 0
        assert orders.orders[view.order_id].status is OrderStatus.UNKNOWN
        assert adapter.calls == [view.client_order_id]


async def test_venue_rejected_is_terminal_rejected(outbox, orders, clock):
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    report = await _run(outbox, orders, ScriptedAdapter(on_place=_rejecting()), clock)
    assert report.rejected == 1
    assert orders.orders[view.order_id].status is OrderStatus.REJECTED
    assert outbox.rows[row_id].state == "DONE"


async def test_ack_without_exchange_order_id_is_unknown(outbox, orders, clock):
    async def hook(order: Order) -> Order:
        return order.model_copy(update={"status": OrderStatus.SUBMITTED})

    view = orders.add(make_order_view())
    await enqueue(outbox, view)
    report = await _run(outbox, orders, ScriptedAdapter(on_place=hook), clock)
    assert report.unknown == 1
    assert orders.events[-1].reason_code == "ACK_WITHOUT_EXCHANGE_ORDER_ID"


# ---- 회로 OPEN / 재시도 / DEAD -----------------------------------------------------
async def test_circuit_open_defers_without_consuming_attempt(outbox, orders, clock):
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    circuit = ExchangeError(ExchangeErrorKind.SERVER_ERROR, retryable=False, circuit_open=True)
    adapter = ScriptedAdapter(on_place=_raising(circuit))
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)

    report = await d.dispatch_once()
    row = outbox.rows[row_id]
    assert report.deferred == 1
    assert (row.state, row.attempt) == ("PENDING", 0)
    assert row.last_error == "NOT_SENT:CIRCUIT_OPEN"
    assert (row.not_before - clock.now).total_seconds() == 20.0
    assert orders.orders[view.order_id].status is OrderStatus.SUBMITTED  # SENT는 이미 기록

    assert (await d.dispatch_once()).claimed == 0  # not_before 미도래
    clock.advance(21)
    adapter._on_place_order = None  # 회로 닫힘
    report = await d.dispatch_once()
    assert report.acknowledged == 1
    assert adapter.lookup_calls == []  # NOT_SENT → 역조회 생략
    assert adapter.calls == [view.client_order_id] * 2


async def test_retryable_after_send_verifies_before_resend(outbox, orders, clock):
    """§5.4 — 전송 후 재시도 가능 오류: 재클레임 시 역조회 먼저, 있으면 채택(재전송 0회)."""
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    venue_copy = Order.model_validate(submit_payload(view)["order"]).model_copy(
        update={"exchange_order_id": "ex-existing", "status": OrderStatus.ACKNOWLEDGED}
    )
    adapter = ScriptedAdapter(
        on_place=_raising(ExchangeError(ExchangeErrorKind.TRANSIENT_NETWORK)),
        lookup={view.client_order_id: venue_copy},
    )
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)

    report = await d.dispatch_once()
    row = outbox.rows[row_id]
    assert report.retried == 1
    assert (row.state, row.attempt) == ("PENDING", 1)
    assert row.last_error == "SENT:TRANSIENT_NETWORK"
    assert row.not_before > clock.now

    clock.advance(120)
    report = await d.dispatch_once()
    assert report.acknowledged == 1
    assert adapter.lookup_calls == [view.client_order_id]
    assert adapter.calls == [view.client_order_id]  # 재전송 없음
    final = orders.orders[view.order_id]
    assert final.exchange_order_id == "ex-existing"
    assert orders.events[-1].reason_code == "RESEND_ADOPTED"


async def test_resend_on_venue_without_client_id_lookup_is_unknown(outbox, orders, clock):
    view = orders.add(make_order_view())
    await enqueue(outbox, view)
    adapter = ScriptedAdapter(
        on_place=_raising(ExchangeError(ExchangeErrorKind.RATE_LIMITED, retry_after_sec=1.0)),
        lookup_unsupported=True,
    )
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)
    assert (await d.dispatch_once()).retried == 1
    clock.advance(120)
    assert (await d.dispatch_once()).unknown == 1
    assert orders.events[-1].reason_code == "RESEND_UNVERIFIABLE"
    assert adapter.calls == [view.client_order_id]


async def test_retry_exhaustion_is_dead_then_unknown(outbox, orders, clock):
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter(on_place=_raising(ExchangeError(ExchangeErrorKind.SERVER_ERROR)))
    d = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, clock=clock, max_attempts=3
    )

    outcomes = []
    for _ in range(3):
        r = await d.dispatch_once()
        outcomes.append((r.retried, r.dead))
        clock.advance(600)
    assert outcomes == [(1, 0), (1, 0), (0, 1)]
    row = outbox.rows[row_id]
    assert (row.state, row.last_error) == ("DEAD", "MAX_ATTEMPTS:SERVER_ERROR")
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.UNKNOWN
    assert orders.events[-1].reason_code == "OUTBOX_DEAD"
    assert len(adapter.calls) == 3 and (await d.dispatch_once()).claimed == 0


async def test_duplicate_client_id_adopts_existing_order(outbox, orders, clock):
    """§6 F14 — DUPLICATE_CLIENT_ID는 중복 방지 성공: 역조회로 채택."""
    view = orders.add(make_order_view())
    existing = Order.model_validate(submit_payload(view)["order"]).model_copy(
        update={"exchange_order_id": "ex-dup", "status": OrderStatus.ACKNOWLEDGED}
    )
    adapter = ScriptedAdapter(
        on_place=_raising(ExchangeError(ExchangeErrorKind.DUPLICATE_CLIENT_ID)),
        lookup={view.client_order_id: existing},
    )
    await enqueue(outbox, view)
    report = await _run(outbox, orders, adapter, clock)
    assert report.acknowledged == 1
    assert orders.orders[view.order_id].exchange_order_id == "ex-dup"
    assert orders.events[-1].reason_code == "DUPLICATE_ADOPTED"


# ---- 게이트 / payload (negative, I-01·I-10) -------------------------------------------
async def test_gate_deny_blocks_send_and_deads_row(outbox, orders, clock):
    """CA 2026-09-06 — 전송 전 DEAD(어댑터 호출 0회) → SEND_ABANDONED로 FAILED."""
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock, gate=deny_gate)

    assert report.gate_denied == 1 and adapter.calls == []
    row = outbox.rows[row_id]
    assert (row.state, row.last_error) == ("DEAD", "SEND_GATE_DENIED")
    abandoned = orders.orders[view.order_id]
    assert (abandoned.status, abandoned.version) == (OrderStatus.FAILED, 2)
    assert [e.event for e in orders.events] == ["SEND_ABANDONED"]
    assert orders.events[-1].reason_code == "SEND_GATE_DENIED"


async def test_gate_deny_after_resend_leaves_order_unknown(outbox, orders, clock):
    """게이트 거부 시점에 주문이 이미 SUBMITTED(재클레임)면 어댑터 호출 이력이
    불확실하므로 FAILED로 확정하지 않고 UNKNOWN(역조회 대상)으로 남긴다."""
    view = orders.add(make_order_view(status=OrderStatus.SUBMITTED))
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock, gate=deny_gate)

    assert report.gate_denied == 1 and adapter.calls == []
    assert outbox.rows[row_id].last_error == "SEND_GATE_DENIED"
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.UNKNOWN and final.unknown_since == clock.now
    assert orders.events[-1].event == "RESPONSE_LOST"


async def test_gate_is_required_and_none_is_rejected(outbox, orders):
    assert gate_param_has_no_default()
    with pytest.raises(ValueError, match="I-01"):
        OutboxDispatcher(
            FakePool(),  # type: ignore[arg-type]
            outbox_repo=outbox, order_repo=orders,
            resolve_adapter=None,  # type: ignore[arg-type]
            pre_send_gate=None,  # type: ignore[arg-type]
            worker_id="w",
        )


async def test_payload_for_other_order_is_dead_without_send(outbox, orders, clock):
    """CA 2026-09-06 — payload fail-closed도 전송 전 DEAD → SEND_ABANDONED/FAILED."""
    view = orders.add(make_order_view())
    payload = submit_payload(view, client_order_id="a-other")
    row_id = await enqueue(outbox, view, payload=payload)
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock)
    assert report.dead == 1 and adapter.calls == []
    assert outbox.rows[row_id].last_error == "PAYLOAD_INVALID"
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.FAILED and final.version == 2
    assert orders.events[-1].reason_code == "PAYLOAD_INVALID"


async def test_progressed_order_consumes_command_without_send(outbox, orders, clock):
    view = orders.add(make_order_view(status=OrderStatus.UNKNOWN))
    row_id = await enqueue(outbox, view)
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock)
    assert report.completed == 1 and adapter.calls == []
    assert outbox.rows[row_id].state == "DONE"


# ---- CANCEL / MODIFY ----------------------------------------------------------------
async def test_cancel_is_done_without_order_transition(outbox, orders, clock):
    view = orders.add(
        make_order_view(status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-1")
    )
    row_id = await enqueue(outbox, view, command_type="CANCEL", payload={})
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock)
    assert report.completed == 1 and adapter.cancel_calls == ["ex-1"]
    assert outbox.rows[row_id].state == "DONE"
    assert orders.orders[view.order_id].version == 1 and orders.events == []


async def test_cancel_before_ack_waits_and_not_found_is_done(outbox, orders, clock):
    pending = orders.add(make_order_view(status=OrderStatus.SUBMITTED))
    row_id = await enqueue(outbox, pending, command_type="CANCEL", payload={})
    adapter = ScriptedAdapter()
    report = await _run(outbox, orders, adapter, clock)
    assert report.retried == 1 and adapter.cancel_calls == []
    assert outbox.rows[row_id].last_error == "NOT_SENT:AWAITING_ACK"

    async def not_found(order_id: str) -> bool:
        raise ExchangeError(ExchangeErrorKind.ORDER_NOT_FOUND)

    acked = orders.add(
        make_order_view(status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-2")
    )
    row2 = await enqueue(outbox, acked, command_type="CANCEL", payload={})
    adapter = ScriptedAdapter(on_cancel=not_found)
    report = await _run(outbox, orders, adapter, clock)
    assert report.completed == 1 and outbox.rows[row2].state == "DONE"


async def test_modify_applies_venue_price_and_quantity(outbox, orders, clock):
    view = orders.add(
        make_order_view(status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-3")
    )
    changes = {"price": "101", "quantity": "2"}
    await enqueue(outbox, view, command_type="MODIFY", payload={"changes": changes})

    async def modify(order_id: str, **kwargs: Any) -> Order:
        base = Order.model_validate(submit_payload(view)["order"])
        return base.model_copy(update={
            "exchange_order_id": order_id, "quantity": Decimal("2"),
            "price": Money(amount=Decimal("101"), currency=Currency.USDT),
        })

    adapter = ScriptedAdapter(on_modify=modify)
    report = await _run(outbox, orders, adapter, clock)
    assert report.acknowledged == 1
    assert adapter.modify_calls == [("ex-3", {"price": "101", "quantity": "2"})]
    final = orders.orders[view.order_id]
    assert (final.price, final.quantity, final.version) == (Decimal("101"), Decimal("2"), 2)
    assert orders.events[-1].event == "MODIFIED"


# ---- 분류표 / run_forever / wiring ---------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (SentUnknownError(), OutcomeKind.UNKNOWN),
        (ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS), OutcomeKind.REJECTED),
        (ExchangeError(ExchangeErrorKind.AUTH), OutcomeKind.REJECTED),
        (ExchangeError(ExchangeErrorKind.UNKNOWN_RESPONSE), OutcomeKind.UNKNOWN),
        (ExchangeError(ExchangeErrorKind.ORDER_NOT_FOUND), OutcomeKind.UNKNOWN),
        (ExchangeError(ExchangeErrorKind.RATE_LIMITED), OutcomeKind.RETRY),
        (ExchangeError(ExchangeErrorKind.CLOCK_SKEW), OutcomeKind.RETRY),
        (ExchangeError(ExchangeErrorKind.SERVER_ERROR, circuit_open=True), OutcomeKind.DEFER),
        (RetryableExchangeError("legacy"), OutcomeKind.UNKNOWN),
    ],
)
def test_submit_failure_classification(exc, kind):
    outcome = classify_submit_failure(exc)
    assert outcome.kind is kind
    assert outcome.not_sent is (kind is OutcomeKind.DEFER)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ExchangeError(ExchangeErrorKind.ORDER_NOT_FOUND), OutcomeKind.DONE),
        (ExchangeError(ExchangeErrorKind.INVALID_ORDER), OutcomeKind.DEAD),
        (ExchangeError(ExchangeErrorKind.UNKNOWN_RESPONSE), OutcomeKind.RETRY),
        (SentUnknownError(), OutcomeKind.RETRY),
        (RuntimeError("x"), OutcomeKind.RETRY),
    ],
)
def test_idempotent_failure_classification(exc, kind):
    assert classify_idempotent_failure(exc).kind is kind


async def test_run_forever_survives_claim_failure_and_polls(outbox, orders, clock):
    view = orders.add(make_order_view())
    await enqueue(outbox, view)
    sleeps: list[float] = []
    original = outbox.claim_batch
    failures = {"left": 1}

    async def flaky_claim(conn, **kw):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("db hiccup")
        return await original(conn, **kw)

    outbox.claim_batch = flaky_claim  # type: ignore[method-assign]

    async def counting_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    d = make_dispatcher(
        outbox=outbox, orders=orders, adapter=ScriptedAdapter(), clock=clock, sleep=counting_sleep
    )
    with pytest.raises(asyncio.CancelledError):
        await d.run_forever()
    # 1) claim 실패 → poll 대기 2) 1행 처리 → 즉시 재폴링(0.0) 3) 빈 큐 → poll 대기
    assert sleeps == [0.1, 0.0, 0.1]
    assert orders.orders[view.order_id].status is OrderStatus.ACKNOWLEDGED


async def test_wiring_uses_foundation_gate(monkeypatch, outbox, orders):
    """I-10 — wiring이 실행 루프와 같은 foundation 게이트를 주입하는지 증명."""
    seen: dict[str, Any] = {}

    def spy(pool: Any, *, require_mandate: bool) -> Any:
        seen["pool"], seen["require_mandate"] = pool, require_mandate
        return allow_gate

    monkeypatch.setattr(wiring, "make_foundation_pre_submit_gate", spy)
    pool = FakePool()

    async def resolve(tenant_id, exchange):
        raise AssertionError("호출되면 안 됨")

    d = wiring.build_outbox_dispatcher(
        pool,  # type: ignore[arg-type]
        resolve_adapter=resolve, outbox_repo=outbox, order_repo=orders,
    )
    assert seen == {"pool": pool, "require_mandate": True}
    assert d._gate is allow_gate
    assert len(d.worker_id.split(":")) == 3
