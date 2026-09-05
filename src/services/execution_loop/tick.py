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
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.execution_loop.candle_history import CandleHistoryCache
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.fsm_writer import _make_fsm_state_writer
from src.services.execution_loop.market_state import build_market_state
from src.services.execution_loop.pending_fill import handle_pending_fill_check
from src.services.execution_loop.position import compute_position_quantity
from src.services.execution_loop.pre_submit_check import is_submission_allowed
from src.services.execution_loop.risk_inputs_assembler import RiskInputCaches
from src.services.execution_loop.tick_risk_phase import (
    default_recorder,
    run_pre_trade_risk_phase,
)
from src.services.order_service.gate import PreSubmitGate
from src.services.order_service.submit import PublishFn
from src.services.risk_decision_recorder import RiskDecisionRecorder
from src.services.safety.distrust_wiring import check_and_persist_distrust
from src.services.safety.reference_quotes import ReferenceQuoteProvider

_ENTRY_DENYING_LEVELS = frozenset({DataDistrustLevel.SUSPICIOUS, DataDistrustLevel.DISTRUSTED})

logger = logging.getLogger(__name__)

_PENDING_STATES = (FSMState.BUY_ORDER_PENDING, FSMState.SELL_ORDER_PENDING, FSMState.STOP_LOSS)


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
    distrust_monitor: DataDistrustMonitor | None = None,
    distrust_providers: Sequence[ReferenceQuoteProvider] = (),
    recorder: RiskDecisionRecorder | None = None,
    candle_cache: CandleHistoryCache | None = None,
) -> None:
    execution, fsm_config, certified_badge = await _load_execution_context(pool, execution_id)
    current_fsm_state = FSMState(execution["fsm_state"])

    if current_fsm_state in _PENDING_STATES:
        # 레드팀 #23-c — paused_by 체크보다 이 분기를 먼저 둔다. 정지된
        # 실행이라도 이미 제출한 주문의 체결 여부는 계속 확인해야 한다 —
        # 그렇지 않으면 주문 제출 직후 일시정지된 실행은 그 주문이 실제로
        # 체결됐는지 다시는 확인되지 않고, fsm_state가 실제 거래소 상태와
        # 영구히 어긋난 채로 남는다.
        await handle_pending_fill_check(
            pool, adapter, execution_id, fsm_config, current_fsm_state, publish
        )
        return

    if execution["paused_by"] is not None:
        return  # 사용자/안전장치가 정지시킨 실행은 새 신호 평가 대상이 아님(FD-16.3)

    symbol = fsm_config.target_asset
    candles = await adapter.get_ohlcv(symbol, "1m", limit=100)
    market_state = build_market_state(fsm_config, candles)

    # R-48 — 캔들 수집 직후 데이터 신뢰도를 매 틱 관측·영속한다(신호가
    # 없어도 상태는 계속 최신으로 유지해야 관측 공백이 없다). 실제 주문
    # 거부 여부는 신호가 나온 뒤, 이게 신규 진입인지 청산/축소인지 알 수
    # 있는 시점에 판단한다(아래 distrust_level 사용부 참조).
    distrust_level = DataDistrustLevel.NORMAL
    if distrust_monitor is not None:
        primary_ticker = await adapter.get_ticker(symbol)
        distrust_level = await check_and_persist_distrust(
            pool,
            distrust_monitor,
            distrust_providers,
            exchange=adapter.get_capabilities().exchange_name,
            symbol=symbol,
            primary=primary_ticker,
            candles=candles,
        )

    signal = strategy_engine.evaluate(
        fsm_config, market_state, execution_id=execution_id, fsm_state=current_fsm_state
    )
    if signal is None:
        return

    if distrust_level == DataDistrustLevel.DISTRUSTED:
        logger.info(
            "run_execution_tick(execution_id=%s): DISTRUSTED 데이터 — 신규 주문 건너뜁니다.",
            execution_id,
        )
        return  # 모든 신규 주문 거부(청산도) — 다음 틱 재평가
    if distrust_level == DataDistrustLevel.SUSPICIOUS and current_fsm_state == FSMState.IDLE:
        logger.info(
            "run_execution_tick(execution_id=%s): SUSPICIOUS 데이터 — 신규 진입만 건너뜁니다.",
            execution_id,
        )
        return  # 신규 진입만 거부 — HOLDING(청산/축소)은 아래로 계속 진행

    position_quantity = await compute_position_quantity(pool, execution_id)
    current_price = candles[-1].close if candles else None

    balances = await adapter.get_balance()
    # Phase 1은 크립토 현물+USDT 페어 전용 — 단일 기준통화 잔고로 총자산을
    # 근사한다(11번 §11.1 FX 계층 도입 전까지의 임시 경계, watchdog.py의
    # compute_equity 콜백 편차와 동일 원칙).
    usdt_balance = next((b for b in balances if b.asset == "USDT"), None)
    total_equity = usdt_balance.total if usdt_balance is not None else Decimal("0")

    allocation = portfolio_engine.allocate(
        signal,
        {
            "allocated_capital": execution["allocated_capital"],
            "position_quantity": position_quantity,
            "current_price": current_price,
            "total_equity": total_equity,
        },
    )
    if allocation is None or not candles:
        return  # 현재가 조회 실패 등 — 다음 틱 재시도(FD-8.2 예외상황)

    # R-32 §3.9 t0~t6 — tick_risk_phase.py. 거부·허용 모두 recorder가 WORM 기록한
    # 뒤, 실행 불가면 FSM을 건드리지 않고 return(다음 틱 재평가). recorder 미주입
    # 시에도 기록은 생략되지 않는다(default_recorder, I-10).
    phase = await run_pre_trade_risk_phase(
        pool,
        adapter,
        execution_id=execution_id,
        user_id=execution["user_id"],
        certified_badge=certified_badge,
        allocated_capital=execution["allocated_capital"],
        signal=signal,
        allocation=allocation,
        candles=candles,
        balances=balances,
        position_quantity=position_quantity,
        distrust_level=distrust_level.value,
        risk_engine=risk_engine,
        recorder=recorder if recorder is not None else default_recorder(pool),
        caches=RiskInputCaches(equity_tracker=equity_tracker),
        candle_cache=candle_cache if candle_cache is not None else CandleHistoryCache(),
        policy=policy,
        now=datetime.now(timezone.utc),
    )
    if phase is None:
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
        observed_fence=phase.fence_snapshot,  # R-36 — PRE_TRADE가 관측한 F0 관통
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
        phase.allocation,
        phase.risk_result,  # decision_id 실림 — submit_order까지 관통(§3.9 끝문단)
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
