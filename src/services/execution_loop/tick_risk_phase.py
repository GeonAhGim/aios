"""R-32 — tick 안의 사전 검사 시퀀스. tick.py에서 분할(L4_risk_and_safety §3.9, §9 R-32).

t0 intent → t1 lookback 캔들(캐시) → t2 assemble_risk_inputs(R-31) → t3
check_decision(R-17) → t4 recorder.record(R-25, 거부·허용 모두 WORM) → t5
is_actionable 아니면 None(호출자는 FSM 미접촉 return) → t6 REDUCE면
approved_quantity를 obligation 값으로 축소. t7는 tick.py에 남는다.
FPO 제약: `check_decision(inputs)`는 gate_kind/trace_id/ttl을 받지 않고 PRE_TRADE·
pre_trade_sec를 내부 고정한다. `OrderIntent.from_allocation`은 FPO `inputs.py`에
없어 여기서 조립한다. R-31이 `None`으로 두는 필드는 호출자가 실제로 아는 값으로만
채운다 — certified_badge/allocated_capital(행), data_distrust_level(이 tick이 방금
관측), connection_fresh(이 tick의 adapter 호출이 방금 성공), active_control_scopes
(`list_active_controls` 1회 — kill switch 우회불가), gross_leverage(포지션 0개=1x).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.event_bus.in_process import InProcessEventBus
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.decision import RiskDecision, RiskOutcome
from src.core.risk.engine import RiskEngine
from src.core.risk.inputs import OrderIntent
from src.core.risk.models import RiskCheckResult
from src.core.strategy.models import Signal
from src.data.models.market_data import Candle
from src.data.models.trading import AccountBalance
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.execution_loop.candle_history import CandleHistoryCache
from src.services.execution_loop.risk_inputs_assembler import RiskInputCaches, assemble_risk_inputs
from src.services.risk_decision_recorder import RiskDecisionRecorder

RECORDER_ACTOR = "execution_loop.tick"
_REDUCE_PREFIX = "REDUCE_QUANTITY_TO:"


@dataclass(frozen=True)
class RiskPhaseOutcome:
    allocation: AllocationDecision  # REDUCE면 approved_quantity가 축소된 사본
    risk_result: RiskCheckResult  # decision_id 실림 — executor→submit_order로 관통
    decision: RiskDecision
    fence_snapshot: dict[str, int]  # t7 pre_submit observed_fence(R-36)로 관통


def default_recorder(pool: asyncpg.Pool) -> RiskDecisionRecorder:
    """recorder 미주입이어도 WORM insert·audit_log는 생략되지 않는다(I-10).
    구독자 없는 in-process bus라 이벤트만 유실된다(§5 허용)."""
    return RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), InProcessEventBus())


def build_order_intent(
    allocation: AllocationDecision, signal: Signal, *, ref_price: Decimal, reduce_only: bool
) -> OrderIntent:
    """t0 — asset_class는 Phase 1 크립토 현물 고정(executor.py와 동일)."""
    return OrderIntent(
        symbol=allocation.symbol, asset_class="CRYPTO_SPOT", side=signal.direction.value,
        quantity=allocation.approved_quantity, ref_price=ref_price,
        notional=allocation.approved_quantity * ref_price, reduce_only=reduce_only,
        strategy_id=allocation.strategy_id, strategy_version=signal.strategy_version,
        capital_pct=allocation.capital_pct,
    )


def reduced_quantity(decision: RiskDecision) -> Decimal:
    """t6 — `REDUCE_QUANTITY_TO:<qty>` obligation이 없으면 fail-closed 예외."""
    for obligation in decision.obligations:
        if obligation.startswith(_REDUCE_PREFIX):
            return Decimal(obligation[len(_REDUCE_PREFIX):])
    raise ValueError(f"REDUCE 결정 {decision.decision_id}에 {_REDUCE_PREFIX} obligation 없음")


async def run_pre_trade_risk_phase(
    pool: asyncpg.Pool, adapter: ExchangeAdapter, *, execution_id: int, user_id: UUID,
    certified_badge: bool, allocated_capital: Decimal, signal: Signal,
    allocation: AllocationDecision, candles: list[Candle], balances: Sequence[AccountBalance],
    position_quantity: Decimal, distrust_level: str, risk_engine: RiskEngine,
    recorder: RiskDecisionRecorder, caches: RiskInputCaches, candle_cache: CandleHistoryCache,
    policy: RiskPolicy, now: datetime,
) -> RiskPhaseOutcome | None:
    """None이면 호출자는 FSM을 건드리지 않고 이번 tick을 끝낸다(t5)."""
    intent = build_order_intent(  # t0
        allocation, signal, ref_price=candles[-1].close, reduce_only=position_quantity != 0
    )
    history = await candle_cache.get(adapter, intent.symbol, bars=policy.var.lookback_bars + 1)
    inputs = await assemble_risk_inputs(  # t2 — t1 실패(None)는 VaR 결손 → I2 DENY
        pool, caches, execution_id=execution_id, user_id=user_id, intent=intent,
        balances=balances, candles=history if history is not None else [], policy=policy, now=now,
    )
    controls = await PostgresRiskGateRepository(pool).list_active_controls(
        tenant_id=user_id, provider_code=adapter.get_capabilities().exchange_name
    )
    exposure = inputs.exposure  # 포지션 0개=무레버리지(R-31 to_legacy_dict와 동일 규칙)
    if exposure.gross_leverage is None and exposure.open_positions_count == 0:
        exposure = exposure.model_copy(update={"gross_leverage": Decimal("1")})
    inputs = inputs.model_copy(update={
        "certified_badge": certified_badge, "allocated_capital": allocated_capital,
        "exposure": exposure, "safety": inputs.safety.model_copy(update={
            "active_control_scopes": tuple(c.scope.value for c in controls),
            "data_distrust_level": distrust_level, "connection_fresh": True}),
    })
    decision = risk_engine.check_decision(inputs)  # t3
    await recorder.record(decision, inputs, actor=RECORDER_ACTOR)  # t4 — 거부도 기록
    if not decision.is_actionable(now):
        return None  # t5
    if decision.outcome == RiskOutcome.REDUCE:  # t6
        allocation = allocation.model_copy(update={"approved_quantity": reduced_quantity(decision)})
    return RiskPhaseOutcome(
        allocation, RiskCheckResult(approved=True, decision_id=decision.decision_id), decision,
        dict(inputs.safety.fence_snapshot or {}),
    )
