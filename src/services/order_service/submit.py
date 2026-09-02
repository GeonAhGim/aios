"""FD-4.2 — 주문 전송(멱등성 확인 → 거래소 전송 → DB 영속화 → 이벤트 발행).

Spec: 기능설계문서_v1.21.md#FD-4.2

트리거: FD-8.4(Executor)가 매매 판단을 내린 직후. 판단(주문을 낼지 말지,
얼마나)은 FD-8의 책임이고, 이 함수는 "이미 승인된 주문을 어떻게 안전하게
전송·추적하는가"만 다룬다(8.2-A 경계선).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.order_service import repository
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate
from src.services.order_service.position_ledger import record_fill_in_position_ledger

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


async def submit_order(
    order: Order,
    *,
    user_id: UUID,
    adapter: ExchangeAdapter,
    pool: asyncpg.Pool,
    publish: PublishFn | None = None,
    pre_submit_gate: PreSubmitGate | None = None,
    mandate_revision_id: UUID | None = None,
) -> Order:
    # 전수감사 §6 / FND-06 배선 — 클레임(아래 a)보다 먼저 검사한다. 거부된
    # 시도는 애초에 orders 테이블에 흔적을 남기지 않는다(클레임 후 거부하면
    # "제출 안 됐지만 claim 행은 남은" 상태를 별도로 청소해야 함).
    # `pre_submit_gate`가 없으면(기본값) 기존 동작과 완전히 동일 — 이미
    # 존재하는 실행 전부에 대한 회귀 없음(마감 게이트 없이 그대로 통과).
    if pre_submit_gate is not None:
        decision = await pre_submit_gate(
            OrderContext(
                user_id=user_id,
                execution_id=order.execution_id,
                exchange=order.exchange,
                mandate_revision_id=mandate_revision_id,
            )
        )
        if decision.outcome != GateOutcome.ALLOW:
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
    # (RetryableExchangeError) 등 실제 예외가 나면, "전송 실패는 DB에 아무
    # 흔적도 남기지 않는다"는 기존 불변조건을 지키기 위해 claim 행을 지우고
    # 그대로 전파한다 — 재시도는 같은(또는 새) client_order_id로 이 함수를
    # 처음부터 다시 거쳐야 한다(이 함수 내부에서 자체 재시도하지 않는다).
    try:
        submitted = await adapter.place_order(claimed)
    except Exception:
        async with pool.acquire() as conn:
            await repository.delete(conn, claimed.order_id)
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
    await record_fill_in_position_ledger(pool, persisted)

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
) -> Order:
    """제출 직후(동기 체결) 또는 이후 폴링(FD-3.4)으로 체결이 확인됐을 때
    상태를 FILLED로 갱신한다 — Executor.execute()와 실행 루프(오케스트레이터)
    양쪽이 공유하는 갱신 경로(FD-8.4 처리단계 5의 전제)."""
    updated = order.model_copy(
        update={
            "exchange_order_id": exchange_order_id,
            "status": OrderStatus.FILLED,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
        }
    )
    async with pool.acquire() as conn:
        persisted = await repository.update_from_exchange(
            conn, updated, expected_status=order.status
        )

    # PM 배정(agent-platform-12, 2026-09-02) — position_ledger.py 참조.
    await record_fill_in_position_ledger(pool, persisted)

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
