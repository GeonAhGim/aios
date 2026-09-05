"""FSM 상태 조건부 쓰기(105번 표준 conditional UPDATE) — tick.py에서 분할(R-32 300줄 상한).

`_make_fsm_state_writer` 이름은 기존 호출부·테스트 호환을 위해 그대로 둔다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.data.models.strategy_fsm import FSMState

FsmStateWriter = Callable[[int, FSMState, FSMState], Awaitable[None]]


async def _make_fsm_state_writer(pool: asyncpg.Pool) -> FsmStateWriter:
    async def writer(execution_id: int, expected_state: FSMState, new_state: FSMState) -> None:
        """레드팀 #2026-09-02-22 — 이전엔 조건없이 덮어써서, 같은 execution_id에
        대한 두 tick이 동시에 IDLE을 읽고 둘 다 PENDING으로 쓰면 나중 것이
        조용히 이겨 fsm_state 자체는 하나로 수렴하지만, 그 사이 두 tick 모두
        이미 실제 주문 제출(Executor.execute) 단계까지 진행해버릴 수 있었다.
        지금은 `expected_state`(이 tick이 애초에 읽은 값)를 조건으로 걸어,
        두 tick 중 나중에 도착한 쪽은 여기서 ConcurrencyConflictError를
        맞고 그 뒤의 Executor.execute() 호출까지 가지 못하고 멈춘다."""
        async with pool.acquire() as conn:
            await conditional_update(
                conn,
                table="strategy_executions",
                id_column="id",
                id_value=execution_id,
                expected_state_column="fsm_state",
                expected_state_value=expected_state.value,
                set_values={"fsm_state": new_state.value},
            )

    return writer
