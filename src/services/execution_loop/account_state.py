"""FD-8.3 RiskEngine.check()가 소비하는 `account_state` 딕셔너리 조립.

RiskEngine 자체는 순수 임계치 비교만 한다(src/core/risk/engine.py 참조) —
이 함수가 실제 DB·거래소 조회를 전부 맡아 그 값을 채운다. 8.2-A 원칙상
RiskEngine의 판단 로직 자체는 이 조립 과정과 분리돼 있어야 감사 가능성이
유지된다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from src.core.loader.risk_policy_loader import RiskPolicy
from src.data.models.market_data import Candle
from src.services.execution_loop.correlation import aggregate_correlated_exposure_pct
from src.services.execution_loop.equity_tracker import (
    ExecutionEquityTracker,
    record_and_persist_equity,
)
from src.services.execution_loop.position import compute_user_positions
from src.services.execution_loop.var_estimator import estimate_var_pct
from src.services.order_service.repository import count_recent_trades


async def assemble_account_state(
    pool: asyncpg.Pool,
    *,
    execution_id: int,
    user_id: UUID,
    symbol: str,
    position_quantity: Decimal,
    total_equity: Decimal,
    available_balance: Decimal,
    allocated_capital: Decimal,
    certified_badge: bool,
    candles: list[Candle],
    equity_tracker: ExecutionEquityTracker,
    policy: RiskPolicy,
) -> dict[str, Any]:
    # PM 배정 ③(agent-platform-12, 2026-09-02) — 일손실/MDD 기준점을
    # strategy_executions(equity_day_start_date/value, equity_peak_value —
    # 마이그레이션 4747bb11f733)에 write-through로 영속화. 재시작 후에도
    # seed()로 복구되므로 "재시작 직후엔 오늘 손실이 0으로 보임" 문제가
    # 사라진다(equity_tracker.py 참조).
    daily_pnl_pct, drawdown_pct = await record_and_persist_equity(
        pool, equity_tracker, execution_id, total_equity
    )

    var_pct = estimate_var_pct(
        candles, confidence=policy.var.confidence, horizon_days=policy.var.horizon_days
    )

    current_price = candles[-1].close if candles else None
    current_prices = {symbol: current_price} if current_price is not None else {}
    positions = await compute_user_positions(pool, user_id, current_prices=current_prices)
    correlated_exposure_pct = aggregate_correlated_exposure_pct(
        symbol,
        threshold=policy.correlation_risk.threshold,
        positions=positions,
        total_equity=total_equity,
    )

    async with pool.acquire() as conn:
        recent_trade_count_1h = await count_recent_trades(
            conn, execution_id, since_hours=Decimal("1")
        )
        trade_count_24h = await count_recent_trades(
            conn, execution_id, since_hours=Decimal("24")
        )
        safety_row = await conn.fetchrow(
            "SELECT circuit_breaker_level FROM system_safety_state WHERE id = 1"
        )
        execution_row = await conn.fetchrow(
            "SELECT paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
        # 사용자 승인(2026-09-02) FROZEN 존 수정 — RiskEngine의 "leverage"
        # 검사가 실제로 비교할 값이 없어 이름뿐인 checked 항목이었다.
        # positions.leverage(스키마 기본값 1, c8ead41fd624)는 이미 존재하는
        # 컬럼이라 여기서 읽기만 하면 된다 — 열린 포지션이 없으면(신규
        # 진입) 아직 아무 레버리지도 안 쓴 상태이므로 1.0.
        open_position_leverage = await conn.fetchval(
            "SELECT leverage FROM positions WHERE execution_id = $1 AND closed_at IS NULL "
            "AND quantity <> 0 ORDER BY entry_time DESC LIMIT 1",
            execution_id,
        )

    circuit_breaker_level = safety_row["circuit_breaker_level"] if safety_row else None
    execution_paused_by_safety = (
        execution_row is not None and execution_row["paused_by"] == "SAFETY_LAYER"
    )
    leverage = open_position_leverage if open_position_leverage is not None else Decimal("1")

    return {
        "daily_pnl_pct": daily_pnl_pct,
        "drawdown_pct": drawdown_pct,
        "position_quantity": position_quantity,
        "total_equity": total_equity,
        "certified_badge": certified_badge,
        "allocated_capital": allocated_capital,
        "available_balance": available_balance,
        "var_pct": var_pct,
        "correlated_exposure_pct": correlated_exposure_pct,
        "recent_trade_count_1h": recent_trade_count_1h,
        "avg_trade_count_24h": trade_count_24h / 24.0,
        "circuit_breaker_level": circuit_breaker_level,
        "execution_paused_by_safety": execution_paused_by_safety,
        "leverage": leverage,
    }
