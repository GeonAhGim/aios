"""PENDING류 실행의 체결 재확인 경로 — tick.py에서 분할(R-32 300줄 상한).

PENDING 상태 실행은 새 신호를 평가하지 않고 이전 주문의 체결 여부만 재확인한다
(FD-3.4 재사용). 체결 없이 종결(REJECTED 등)되면 신호평가 이전 상태로 되돌려
fsm_state가 PENDING에 영구히 갇히는 결함(#2026-09-02-39류)을 막는다.
"""
from __future__ import annotations

import logging

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.executor.executor import next_fsm_state_after_fill
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.condition_compiler import ORDER_FILLED
from src.services.execution_loop.fsm_writer import _make_fsm_state_writer
from src.services.order_service import repository
from src.services.order_service.submit import PublishFn, apply_fill

logger = logging.getLogger(__name__)

_FINAL_ORDER_STATUSES = frozenset({"FILLED", "REJECTED", "CANCELLED", "EXPIRED", "FAILED"})
# PM 배정(agent-platform-12, 2026-09-02) — 체결 없이 끝난 종결 상태. FILLED는
# 별도 분기(_handle_pending_fill_check 참조)라 여기 포함하지 않는다.
_FAILURE_TERMINAL_STATUSES = frozenset({"REJECTED", "CANCELLED", "EXPIRED", "FAILED"})


def _previous_fsm_state(fsm_config: FSMStrategyConfig, pending_state: FSMState) -> FSMState:
    """`pending_state`로 들어오는 신호평가 전이(ORDER_FILLED가 아닌 것)의
    from_state를 찾는다 — 주문이 체결 없이 종결됐을 때 되돌아갈 상태.
    컴파일러가 만드는 고정 FSM 모양(IDLE→BUY_ORDER_PENDING, HOLDING→
    SELL_ORDER_PENDING/STOP_LOSS)에서는 항상 정확히 하나 존재한다."""
    for transition in fsm_config.transitions:
        if transition.to_state == pending_state and transition.condition != ORDER_FILLED:
            return transition.from_state
    raise ValueError(f"{pending_state}로 들어오는 전이가 FSM에 없습니다 — FSM 정의 오류")


async def handle_pending_fill_check(
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    execution_id: int,
    fsm_config: FSMStrategyConfig,
    pending_state: FSMState,
    publish: PublishFn | None,
) -> None:
    """PENDING 상태 실행은 새 신호를 평가하지 않는다 — 이전에 제출한
    주문의 체결 여부만 재확인한다(FD-3.4 재사용, 여러 틱에 걸쳐 반복
    가능)."""
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT order_id, exchange_order_id, client_order_id, execution_id, status "
            "FROM orders WHERE execution_id = $1 ORDER BY created_at DESC LIMIT 1",
            execution_id,
        )
    if order is None or order["exchange_order_id"] is None:
        return

    if order["status"] in _FAILURE_TERMINAL_STATUSES:
        # PM 배정(agent-platform-12, 2026-09-02) — 이전엔 이 경우 아무것도
        # 안 하고 return해, fsm_state가 PENDING에 영원히 갇혀 이후 어떤
        # 신호도 다시 평가되지 않았다(cancel.py에도 이 되돌림 로직이
        # 없음). 신호평가로 이 PENDING에 들어왔던 이전 상태로 되돌린다.
        previous_state = _previous_fsm_state(fsm_config, pending_state)
        writer = await _make_fsm_state_writer(pool)
        try:
            await writer(execution_id, pending_state, previous_state)
        except ConcurrencyConflictError:
            logger.info(
                "_handle_pending_fill_check(execution_id=%s): fsm_state 복귀 중 "
                "동시성 충돌 — 다음 tick에서 재시도됩니다.",
                execution_id,
            )
        return

    if order["status"] in _FINAL_ORDER_STATUSES:
        return  # FILLED 등 — 이미 이전 틱에서 처리됐어야 정상(방어적 no-op)

    reconfirmed = await adapter.get_order(order["exchange_order_id"])
    if reconfirmed.status.value != "FILLED":
        return

    async with pool.acquire() as conn:
        current = await repository.get_by_order_id(conn, order["order_id"])
    if current is None:
        return

    await apply_fill(
        current,
        exchange_order_id=order["exchange_order_id"],
        filled_quantity=reconfirmed.filled_quantity,
        average_fill_price=reconfirmed.average_fill_price,
        pool=pool,
        publish=publish,
    )
    next_state = next_fsm_state_after_fill(fsm_config, pending_state)
    writer = await _make_fsm_state_writer(pool)
    await writer(execution_id, pending_state, next_state)
