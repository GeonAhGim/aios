"""FD-4.2 — 주문 전송(멱등성 확인 → 거래소 전송 → DB 영속화 → 이벤트 발행).

Spec: 기능설계문서_v1.21.md#FD-4.2

트리거: FD-8.4(Executor)가 매매 판단을 내린 직후. 판단(주문을 낼지 말지,
얼마나)은 FD-8의 책임이고, 이 함수는 "이미 승인된 주문을 어떻게 안전하게
전송·추적하는가"만 다룬다(8.2-A 경계선).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.core.observability.metric_names import (
    ORDER_SUBMIT_COUNT_TOTAL,
    ORDER_SUBMIT_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent, ProviderOrderEvent
from src.services.oms.domain.state_machine import OrderEvent
from src.services.order_service import repository
from src.services.order_service.gate import (
    GateOutcome,
    OrderContext,
    PreSubmitGate,
    record_gate_decision,
)
from src.services.order_service.position_ledger import record_fill_in_position_ledger

_oms_orders = PostgresOrderRepository()

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class OrderSubmissionError(Exception):
    """FD-4.1 검증 실패 등 — 거래소 호출 전 단계에서 이미 거부된 경우.
    Executor가 아니라 상위(FD-8.2 Allocation) 로직 버그 신호."""


class OrderDeniedByRiskGateError(Exception):
    """전수감사 §6 — kill switch/mandate 정책이 legacy 주문 경로를 전혀
    막지 못하던 결함(안전 통제가 배선 안 된 병렬 섬)의 수정. `reason_codes`는
    `pre_submit_gate`가 돌려준 것을 그대로 옮긴다."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        self.reason_codes = reason_codes
        super().__init__(f"주문이 위험 게이트에 의해 거부됐습니다: {reason_codes}")


async def _mark_claim_failed(
    conn: asyncpg.Connection, claimed: Order, *, user_id: UUID, reason: str
) -> None:
    """전송 자체가 실패한 claim 행을 CREATED→FAILED로 확정한다(oms의
    `order_repository.transition()` 재사용 — order_events WORM·audit_bridge까지
    같은 tx에서 함께 남는다, task-1566 note 참조). `expected_version=0`은
    claim 직후(다른 전이가 끼어들 수 없는 이 함수 내부 흐름)라 항상 참이다
    — `orders.version` DDL DEFAULT가 0이고(073beca589d5) `repository.insert()`가
    이 컬럼을 건드리지 않는다."""
    event = OrderTransitionEvent(
        order_id=claimed.order_id,
        from_status=OrderStatus.CREATED,
        to_status=OrderStatus.FAILED,
        event=OrderEvent.VALIDATION_FAILED.value,
        reason_code=reason,
        actor_subject_id=user_id,
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=datetime.now(timezone.utc),
        payload_hash=hashlib.sha256(f"{claimed.order_id}:{reason}".encode()).hexdigest(),
    )
    await _oms_orders.transition(
        conn,
        order_id=claimed.order_id,
        expected_status=OrderStatus.CREATED,
        expected_version=0,
        new_status=OrderStatus.FAILED,
        patch={},
        event=event,
    )


async def submit_order(
    order: Order,
    *,
    user_id: UUID,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    pre_submit_gate: PreSubmitGate,
    mandate_revision_id: UUID | None = None,
    metrics: MetricsPort | None = None,
) -> Order:
    # PLT-10 — 기본값 NullMetrics: 호출부가 미설정이면(기존 실행 전부) 무영향.
    metrics = metrics if metrics is not None else NullMetrics()
    mode = "paper" if adapter.is_paper_trading else "live"
    submit_started = time.monotonic()

    def _record_submit_outcome(outcome: str) -> None:
        metrics.counter(
            ORDER_SUBMIT_COUNT_TOTAL,
            labels={"exchange": order.exchange, "mode": mode, "outcome": outcome},
        )
        metrics.observe(
            ORDER_SUBMIT_DURATION_SECONDS,
            time.monotonic() - submit_started,
            labels={"exchange": order.exchange},
        )

    # 전수감사 §6 / FND-06 배선 — 클레임(아래 a)보다 먼저 검사한다. 거부된
    # 시도는 애초에 orders 테이블에 흔적을 남기지 않는다(클레임 후 거부하면
    # "제출 안 됐지만 claim 행은 남은" 상태를 별도로 청소해야 함).
    # task-1715(P0-B) — `pre_submit_gate`는 필수 인자다(I-01). None 분기로
    # 통과시키던 기존 fail-open 배선(감사 2026-09-06 P0)을 제거했다 — 게이트를
    # 넘기지 않고는 이 함수를 호출할 수 없다.
    gate_started = time.monotonic()
    decision = await pre_submit_gate(
        OrderContext(
            user_id=user_id,
            execution_id=order.execution_id,
            exchange=order.exchange,
            mandate_revision_id=mandate_revision_id,
        )
    )
    record_gate_decision(metrics, decision, duration_seconds=time.monotonic() - gate_started)
    if decision.outcome != GateOutcome.ALLOW:
        _record_submit_outcome("denied")
        raise OrderDeniedByRiskGateError(decision.reason_codes)

    # FD-4.2-a 멱등성 — 레드팀 #2026-09-02-19 — "먼저 SELECT로 없음을
    # 확인하고, 거래소 전송 후에야 INSERT한다"는 TOCTOU였다: 동시에 같은
    # client_order_id로 두 번 호출되면 둘 다 SELECT를 통과해 거래소에 실제
    # 주문을 두 번 낼 수 있었고, 뒤늦은 INSERT의 UNIQUE 위반은 아무 데서도
    # 잡히지 않아 거래소엔 나갔지만 DB엔 없는 고아 주문이 됐다.
    #
    # 지금은 거래소를 부르기 *전에* client_order_id를 이 INSERT로 원자적
    # 선점한다 — status=CREATED, exchange_order_id=NULL인 "아직 전송 안 됨"
    # 표식 행이다. 두 번째 호출은 UNIQUE 위반으로 이 시점에서 즉시 걸러져
    # 거래소를 아예 부르지 않는다(이미 있는 행을 그대로 반환).
    async with pool.acquire() as conn:
        try:
            claimed = await repository.insert(conn, order, user_id=user_id)
        except asyncpg.UniqueViolationError:
            existing = await repository.get_by_client_order_id(conn, order.client_order_id)
            if existing is None:
                raise  # UNIQUE 위반인데 그 행이 없다 — 예상 못한 상태, 그대로 전파
            return existing

    # FD-4.2-b 거래소 전송 — REJECTED는 예외가 아니라 정상 흐름(place_order가
    # status=REJECTED로 반환, 아래에서 그대로 영속화). 네트워크 오류
    # (RetryableExchangeError) 등 실제 예외가 나면, 재시도는 같은(또는 새)
    # client_order_id로 이 함수를 처음부터 다시 거쳐야 한다(이 함수 내부에서
    # 자체 재시도하지 않는다) — 그대로 전파한다.
    #
    # task-1566(L4-09) 편차 — 예전엔 "전송 실패는 DB에 아무 흔적도 남기지
    # 않는다"며 claim 행을 지웠다(레드팀 #2026-09-02-19). 지금은 oms의
    # order_repository.transition()으로 CREATED→FAILED 확정만 하고 행은
    # 남긴다 — 지우면 "제출을 시도했다는 사실 자체"가 감사 흔적 없이
    # 사라지고, L4-09 cutover 이후 이 경로가 OMS의 유일한 전이 경로가
    # 되려면 삭제가 아니라 상태기계 전이로 실패를 표현해야 한다
    # (order_events WORM에 VALIDATION_FAILED로 남는다).
    try:
        submitted = await adapter.place_order(claimed)
    except Exception:
        async with pool.acquire() as conn, conn.transaction():
            await _mark_claim_failed(conn, claimed, user_id=user_id, reason="EXCHANGE_SEND_ERROR")
        _record_submit_outcome("error")
        raise

    # FD-4.2-c DB 영속화 — claimed 행을 실제 거래소 응답으로 갱신한다.
    # 이벤트 발행보다 먼저 커밋(05번 §5.6).
    async with pool.acquire() as conn:
        persisted = await repository.update_from_exchange(
            conn, submitted, expected_status=claimed.status
        )

    # PM 배정(agent-platform-12, 2026-09-02) — 거래소가 place_order 응답에서
    # 즉시 FILLED를 돌려주는 동기체결 케이스. apply_fill()을 거치지 않고
    # 여기서 바로 확정되므로, positions 기록도 이 지점에서 해야 놓치지
    # 않는다(position_ledger.py 참조 — 다른 호출부는 apply_fill 쪽).
    await record_fill_in_position_ledger(pool, persisted, metrics=metrics)

    _record_submit_outcome("rejected" if persisted.status == OrderStatus.REJECTED else "accepted")

    # FD-4.2-d 이벤트 발행(FD-6.1 재사용).
    if publish is not None:
        await publish(
            "order.status.changed",
            {
                "order_id": str(persisted.order_id),
                "client_order_id": persisted.client_order_id,
                "execution_id": persisted.execution_id,
                "status": persisted.status.value,
            },
        )

    return persisted


async def apply_fill(
    order: Order,
    *,
    exchange_order_id: str,
    filled_quantity: Any,
    average_fill_price: Any,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    metrics: MetricsPort | None = None,
) -> Order:
    """제출 직후(동기 체결) 또는 이후 폴링(FD-3.4)으로 체결이 확인됐을 때
    상태를 FILLED로 갱신한다 — Executor.execute()와 실행 루프(오케스트레이터)
    양쪽이 공유하는 갱신 경로(FD-8.4 처리단계 5의 전제).

    task-1553(L4-15) — 실제 처리는 `oms.application.inbox_processor.
    InboxProcessor.ingest()`로 위임하는 얇은 호환 래퍼로 축소됐다(기존
    호출자 시그니처는 그대로). 폴링에는 거래소 원장의 개별 체결 내역이
    없으므로(누적 filled_quantity/average_fill_price 스냅샷뿐) `fills.
    provider_fill_id`를 `order_id`+`filled_quantity`의 결정론적 함수로
    합성한다 — 같은 스냅샷의 재호출(재시도·중복 폴링)은 항상 같은 키가 되어
    inbox/fills 양쪽의 ON CONFLICT가 흡수한다(§6 F9와 동일 보장)."""
    # 지연 임포트 — inbox_processor.py가 `order_service.repository`/
    # `position_ledger`를 쓰므로(§`_apply_position_ledger`), 모듈 최상단에서
    # 임포트하면 order_service/__init__.py → submit.py → inbox_processor.py →
    # order_service(패키지) 순환 임포트가 된다.
    from src.services.oms.application.inbox_processor import InboxProcessor

    metrics = metrics if metrics is not None else NullMetrics()
    filled_qty = Decimal(str(filled_quantity))
    if isinstance(average_fill_price, Money):
        price_amount, fee_currency = average_fill_price.amount, average_fill_price.currency.value
    elif average_fill_price is not None:
        price_amount, fee_currency = Decimal(str(average_fill_price)), Currency.USDT.value
    else:
        price_amount, fee_currency = Decimal("0"), Currency.USDT.value

    now = datetime.now(timezone.utc)
    provider_id = f"poll:{order.order_id}:{filled_qty}"
    fill = FillEvent(
        provider_fill_id=provider_id,
        venue=order.exchange,
        order_id=order.order_id,
        exchange_order_id=exchange_order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=filled_qty,
        price=price_amount,
        fee=Decimal("0"),
        fee_currency=fee_currency,
        liquidity="UNKNOWN",
        venue_ts=now,
    )
    ev = ProviderOrderEvent(
        provider_event_id=provider_id,
        venue=order.exchange,
        venue_symbol=order.symbol,
        exchange_order_id=exchange_order_id,
        client_order_id=order.client_order_id,
        venue_status="FILLED",
        filled_quantity=filled_qty,
        average_price=price_amount,
        last_fill=fill,
        venue_ts=now,
        received_at=now,
        source="POLL",
        raw_hash=hashlib.sha256(provider_id.encode()).hexdigest(),
    )
    await InboxProcessor(pool, metrics=metrics).ingest(ev)

    async with pool.acquire() as conn:
        persisted = await repository.get_by_order_id(conn, order.order_id)
    if persisted is None:
        raise RuntimeError(
            f"apply_fill: order_id={order.order_id} 조회 실패 — inbox 처리 후 행이 없습니다."
        )

    if publish is not None:
        await publish(
            "order.status.changed",
            {
                "order_id": str(persisted.order_id),
                "client_order_id": persisted.client_order_id,
                "execution_id": persisted.execution_id,
                "status": persisted.status.value,
            },
        )
    return persisted
