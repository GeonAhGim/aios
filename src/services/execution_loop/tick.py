"""FD-8.1~8.4 실행 루프 — 실행 하나에 대한 한 틱(오케스트레이터).

이 함수 자체는 FD-8의 세부기능 번호를 갖지 않는다 — StrategyEngine/
PortfolioEngine/RiskEngine/Executor(전부 결정론적, 8.2-A)를 순서대로
호출하고 그 사이 FD-8.0(FSM 상태) 전이 시점을 조율하는 배선 코드다.

FSM 전이 시점 설계: IDLE/HOLDING → PENDING 전이는 RiskEngine이 승인한
직후, Executor를 부르기 직전에만 일어난다(신호 생성 직후가 아님) — 그래야
PortfolioEngine이 가격 조회 실패로 틱을 스킵하거나 RiskEngine이 거부해도
fsm_state가 PENDING에 갇히지 않고 다음 틱에 안전하게 재평가된다. Executor
자신의 예외상황 문서("전송 실패 시 fsm_state를 되돌리지 않는다")는 바로 이
직전에 이미 PENDING으로 바뀐 상태를 그대로 둔다는 뜻과 정합한다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.core.executor.executor import Executor, next_fsm_state_after_fill
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.condition_compiler import ORDER_FILLED
from src.services.execution_loop.account_state import assemble_account_state
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.market_state import build_market_state
from src.services.execution_loop.position import compute_position_quantity
from src.services.execution_loop.pre_submit_check import is_submission_allowed
from src.services.order_service import repository
from src.services.order_service.gate import PreSubmitGate
from src.services.order_service.submit import PublishFn, apply_fill

logger = logging.getLogger(__name__)

_PENDING_STATES = (FSMState.BUY_ORDER_PENDING, FSMState.SELL_ORDER_PENDING, FSMState.STOP_LOSS)
_FINAL_ORDER_STATUSES = frozenset({"FILLED", "REJECTED", "CANCELLED", "EXPIRED", "FAILED"})
# PM 배정(agent-platform-12, 2026-09-02) — 체결 없이 끝난 종결 상태. FILLED는
# 별도 분기(_handle_pending_fill_check 참조)라 여기 포함하지 않는다.
_FAILURE_TERMINAL_STATUSES = frozenset({"REJECTED", "CANCELLED", "EXPIRED", "FAILED"})


async def _load_execution_context(
    pool: asyncpg.Pool, execution_id: int
) -> tuple[dict[str, Any], FSMStrategyConfig, bool]:
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT strategy_id, strategy_version, user_id, mode, exchange, "
            "allocated_capital, currency, fsm_state, paused_by "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        if execution is None:
            raise ValueError(f"존재하지 않는 실행입니다: {execution_id}")
        strategy = await conn.fetchrow(
            "SELECT fsm_definition, certified_badge, target_asset FROM strategies "
            "WHERE strategy_id = $1 AND version = $2",
            execution["strategy_id"],
            execution["strategy_version"],
        )
        if strategy is None:
            raise ValueError(
                f"실행 {execution_id}이 참조하는 전략이 없습니다: "
                f"{execution['strategy_id']}@{execution['strategy_version']}"
            )

    fsm_config = FSMStrategyConfig.model_validate(json.loads(strategy["fsm_definition"]))
    return dict(execution), fsm_config, strategy["certified_badge"]


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


def _previous_fsm_state(fsm_config: FSMStrategyConfig, pending_state: FSMState) -> FSMState:
    """`pending_state`로 들어오는 신호평가 전이(ORDER_FILLED가 아닌 것)의
    from_state를 찾는다 — 주문이 체결 없이 종결됐을 때 되돌아갈 상태.
    컴파일러가 만드는 고정 FSM 모양(IDLE→BUY_ORDER_PENDING, HOLDING→
    SELL_ORDER_PENDING/STOP_LOSS)에서는 항상 정확히 하나 존재한다."""
    for transition in fsm_config.transitions:
        if transition.to_state == pending_state and transition.condition != ORDER_FILLED:
            return transition.from_state
    raise ValueError(f"{pending_state}로 들어오는 전이가 FSM에 없습니다 — FSM 정의 오류")


async def _handle_pending_fill_check(
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


async def run_execution_tick(
    pool: asyncpg.Pool,
    adapter: ExchangeAdapter,
    execution_id: int,
    *,
    strategy_engine: StrategyEngine,
    portfolio_engine: PortfolioEngine,
    risk_engine: RiskEngine,
    executor: Executor,
    equity_tracker: ExecutionEquityTracker,
    policy: RiskPolicy,
    publish: PublishFn | None = None,
    pre_submit_gate: PreSubmitGate | None = None,
) -> None:
    execution, fsm_config, certified_badge = await _load_execution_context(pool, execution_id)
    current_fsm_state = FSMState(execution["fsm_state"])

    if current_fsm_state in _PENDING_STATES:
        # 레드팀 #23-c — paused_by 체크보다 이 분기를 먼저 둔다. 정지된
        # 실행이라도 이미 제출한 주문의 체결 여부는 계속 확인해야 한다 —
        # 그렇지 않으면 주문 제출 직후 일시정지된 실행은 그 주문이 실제로
        # 체결됐는지 다시는 확인되지 않고, fsm_state가 실제 거래소 상태와
        # 영구히 어긋난 채로 남는다.
        await _handle_pending_fill_check(
            pool, adapter, execution_id, fsm_config, current_fsm_state, publish
        )
        return

    if execution["paused_by"] is not None:
        return  # 사용자/안전장치가 정지시킨 실행은 새 신호 평가 대상이 아님(FD-16.3)

    symbol = fsm_config.target_asset
    candles = await adapter.get_ohlcv(symbol, "1m", limit=100)
    market_state = build_market_state(fsm_config, candles)

    signal = strategy_engine.evaluate(
        fsm_config, market_state, execution_id=execution_id, fsm_state=current_fsm_state
    )
    if signal is None:
        return

    position_quantity = await compute_position_quantity(pool, execution_id)
    current_price = candles[-1].close if candles else None

    balances = await adapter.get_balance()
    # Phase 1은 크립토 현물+USDT 페어 전용 — 단일 기준통화 잔고로 총자산을
    # 근사한다(11번 §11.1 FX 계층 도입 전까지의 임시 경계, watchdog.py의
    # compute_equity 콜백 편차와 동일 원칙).
    usdt_balance = next((b for b in balances if b.asset == "USDT"), None)
    total_equity = usdt_balance.total if usdt_balance is not None else Decimal("0")
    available_balance = usdt_balance.available if usdt_balance is not None else Decimal("0")

    allocation = portfolio_engine.allocate(
        signal,
        {
            "allocated_capital": execution["allocated_capital"],
            "position_quantity": position_quantity,
            "current_price": current_price,
            "total_equity": total_equity,
        },
    )
    if allocation is None:
        return  # 현재가 조회 실패 등 — 다음 틱 재시도(FD-8.2 예외상황)

    account_state = await assemble_account_state(
        pool,
        execution_id=execution_id,
        user_id=execution["user_id"],
        symbol=symbol,
        position_quantity=position_quantity,
        total_equity=total_equity,
        available_balance=available_balance,
        allocated_capital=execution["allocated_capital"],
        certified_badge=certified_badge,
        candles=candles,
        equity_tracker=equity_tracker,
        policy=policy,
    )
    risk_result = risk_engine.check(allocation, account_state)
    if not risk_result.approved:
        logger.info(
            "RiskEngine 거부(execution_id=%s): %s (checked=%s)",
            execution_id,
            risk_result.rejection_reason,
            risk_result.checked_rules,
        )
        return  # 다음 틱 재평가 — fsm_state는 IDLE/HOLDING 그대로

    # 레드팀 #23-a — 신호 평가·PortfolioEngine·RiskEngine을 거치는 동안
    # Watchdog가 안전정지를 걸었을 수 있다(이번 tick 시작 시점에는
    # paused_by가 비어 있었더라도). fsm_state를 PENDING류로 전환해 실제
    # 주문 제출로 이어지기 직전, 최신 paused_by를 다시 한번 확인한다 —
    # 여기서 걸리면 fsm_state 자체를 아직 안 건드린 상태라 별도 되돌림도
    # 필요 없다.
    async with pool.acquire() as conn:
        still_paused = await conn.fetchval(
            "SELECT paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    if still_paused is not None:
        logger.info(
            "run_execution_tick(execution_id=%s): 신호 평가 중 안전정지 감지 — "
            "주문 제출을 건너뜁니다.",
            execution_id,
        )
        return

    # 전수감사 §6 / FND-06 배선 — FSM을 PENDING류로 전이하기 *전에* 검사한다.
    # executor.py는 FROZEN_PAPER_ONLY라 시그니처를 바꿔 게이트를 그 안까지
    # 관통시키지 않는다 — 여기서 거부하면 executor.execute()를 아예 안 부르므로
    # 동일한 안전효과를 얻으면서 FSM은 전혀 건드리지 않는다(전이 이후에
    # 거부하면 PENDING류에 영구히 갇히는 #2026-09-02-39류 결함을 재현하게 됨).
    allowed = await is_submission_allowed(
        pre_submit_gate,
        user_id=execution["user_id"],
        execution_id=execution_id,
        exchange=adapter.get_capabilities().exchange_name,
    )
    if not allowed:
        return

    writer = await _make_fsm_state_writer(pool)
    try:
        await writer(execution_id, current_fsm_state, signal.to_state)
    except ConcurrencyConflictError:
        # 이 execution_id에 대한 다른 tick이 먼저 IDLE/HOLDING을 선점했다 —
        # RiskEngine 거부와 동일하게 이번 tick은 조용히 포기하고 다음 tick에서
        # 최신 fsm_state로 다시 평가한다(#2026-09-02-22).
        logger.info(
            "run_execution_tick(execution_id=%s): fsm_state 동시성 충돌 — 이번 tick 건너뜁니다.",
            execution_id,
        )
        return

    await executor.execute(
        allocation,
        risk_result,
        adapter,
        execution_id=execution_id,
        user_id=execution["user_id"],
        strategy_version=execution["strategy_version"],
        mode=execution["mode"],
        side=signal.direction,
        pending_fsm_state=signal.to_state,
        fsm_config=fsm_config,
        fsm_state_writer=writer,
        publish=publish,
        pool=pool,
    )
