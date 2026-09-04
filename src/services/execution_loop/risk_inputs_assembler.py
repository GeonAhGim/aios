"""R-31 — execution_loop/risk_inputs_assembler.py

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.2, §3.5, §9 R-31.

`RiskInputs`(`core/risk/inputs.py`, R-03) 조립기 — 이전 `account_state.py`가
tick마다 DB 왕복 5회(daily/peak equity read+write, safety_row, execution_row,
count_recent_trades×2) + `compute_user_positions`로 만들던 미측정 왕복을
**정확히 2회 SELECT**(§3.5 CTE 스냅샷 1회 + `read_fences` 1회)로 줄인다.
R-27/28/29/30을 재구현하지 않고 그대로 조합만 한다:
`load_exposure_snapshot`(R-27, 단일 CTE), `estimate_portfolio_var_es`/
`correlated_exposure`(R-29, 순수함수 — 새 왕복 없음), `record_and_persist_equity`
(R-30 — day_start/peak read-through는 이 execution을 이 프로세스에서 처음
다루는 tick에서만 1회 발생하고 이후로는 UPDATE 1회뿐이다. 이 SELECT는
R-30이 이미 책임지는 기존 경로이지 이 리프가 새로 만드는 왕복이 아니다).

`RiskInputs.tenant_id`는 `to_legacy_dict()`가 필요하지만 `core/risk/inputs.py`
는 FROZEN_PAPER_ONLY(PM 승인 없이 수정 금지)라 `RiskInputs.to_legacy_dict()`
메서드를 추가할 수 없다 — 대신 이 모듈(SCAFFOLD zone)에 같은 이름의 자유
함수 `to_legacy_dict(inputs)`를 둔다. `certified_badge`/`allocated_capital`은
이 함수의 파라미터 목록에 없다(§2 표 원문 그대로) — 호출부가 이미 알고
있는 값이므로 `to_legacy_dict()` 결과에 덮어써야 한다(`account_state.py` 참조).

상관위험 fail-closed 범위: `intent.symbol` 외 다른 심볼의 보유 내역은
`ExposureSnapshot`(R-27)이 심볼별 원자료를 노출하지 않아(ASSET_CLASS로만
집계) 이 리프의 2왕복 예산 안에서는 식별할 수 없다. `gross_tenant ==
gross_symbol`(전체 노출이 이 심볼에만 있음)일 때만 상관을 계산하고, 그 외엔
0.0으로 암묵 치환하지 않고 `missing_pairs`를 채워 DENY로 보낸다(I2/R3).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.data.models.market_data import Candle
from src.data.models.trading import AccountBalance
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.read_fence import read_fence_snapshot
from src.services.execution_loop.correlation_service import correlated_exposure
from src.services.execution_loop.equity_tracker import (
    ExecutionEquityTracker,
    record_and_persist_equity,
)
from src.services.execution_loop.exposure_snapshot import load_exposure_snapshot
from src.services.execution_loop.var_estimator import estimate_portfolio_var_es

_USDT = "USDT"


@dataclass(frozen=True)
class RiskInputCaches:
    """§2 표 `assemble_risk_inputs(pool, caches, ...)`의 `caches` — 지금은
    R-30 equity 기준점(day_start/peak)만 담는다. 호출자가 tick 경계를 넘어
    같은 인스턴스를 재사용해야 `record_and_persist_equity`의 seed SELECT가
    execution당 최초 1회로 끝난다."""

    equity_tracker: ExecutionEquityTracker


async def assemble_risk_inputs(
    pool: asyncpg.Pool,
    caches: RiskInputCaches,
    *,
    execution_id: int,
    user_id: UUID,
    intent: OrderIntent,
    balances: Sequence[AccountBalance],
    candles: list[Candle],
    policy: RiskPolicy,
    now: datetime,
) -> RiskInputs:
    usdt = next((b for b in balances if b.asset == _USDT), None)
    total_equity = usdt.total if usdt is not None else None
    available_balance = usdt.available if usdt is not None else None

    current_price = candles[-1].close if candles else None
    prices = {intent.symbol: current_price} if current_price is not None else {}
    provider = candles[0].exchange if candles else ""

    async with pool.acquire() as conn:
        exposure_row = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol=intent.symbol,
            strategy_id=intent.strategy_id,
            provider=provider,
            prices=prices,
        )

    fence_repo = PostgresRiskGateRepository(pool)
    fence = await read_fence_snapshot(
        fence_repo,
        tenant_id=user_id,
        provider_code=provider,
        execution_ref=f"exec:{execution_id}",
    )

    daily_pnl_pct: Decimal | None
    drawdown_pct: Decimal | None
    day_start_equity: Decimal | None
    peak_equity: Decimal | None
    if total_equity is not None:
        daily_pnl_pct, drawdown_pct = await record_and_persist_equity(
            pool, caches.equity_tracker, execution_id, total_equity
        )
        _day_start_date, day_start_equity = caches.equity_tracker.day_start(execution_id)
        peak_equity = caches.equity_tracker.peak(execution_id)
    else:
        daily_pnl_pct = drawdown_pct = day_start_equity = peak_equity = None

    histories = {intent.symbol: candles} if candles else {}
    var_result = estimate_portfolio_var_es(
        histories, {intent.symbol: Decimal("1")} if candles else {}, policy.var
    )

    only_this_symbol = exposure_row.gross_tenant == exposure_row.gross_symbol
    if only_this_symbol:
        positions = (
            [(intent.symbol, exposure_row.gross_symbol)] if exposure_row.gross_symbol else []
        )
        raw_corr_exposure, max_correlation = correlated_exposure(
            histories,
            positions,
            intent.symbol,
            threshold=policy.correlation_risk.threshold,
            min_overlap=policy.correlation_risk.min_overlap,
        )
        missing_pairs = () if raw_corr_exposure is not None else ("target:insufficient_history",)
    else:
        raw_corr_exposure = None
        max_correlation = None
        missing_pairs = ("exposure:other_symbols_unresolved",)

    if raw_corr_exposure is None or total_equity is None:
        correlated_exposure_pct = None  # 미지 상관 또는 자산 결손 — I2 fail-closed
    elif total_equity <= 0:
        correlated_exposure_pct = Decimal("0")  # 자산 0은 알려진 값(결손이 아니다)
    else:
        correlated_exposure_pct = (raw_corr_exposure / total_equity) * Decimal("100")

    gross_notional = {
        f"TENANT:{user_id}": exposure_row.gross_tenant,
        f"ACCOUNT:{user_id}": exposure_row.gross_tenant,
        f"STRATEGY:{intent.strategy_id}": exposure_row.gross_strategy,
        f"SYMBOL:{intent.symbol}": exposure_row.gross_symbol,
        f"PROVIDER:{provider}": exposure_row.gross_provider,
        **exposure_row.gross_asset_class,
    }
    net_notional = {
        f"TENANT:{user_id}": exposure_row.net_tenant,
        f"ACCOUNT:{user_id}": exposure_row.net_tenant,
    }
    fence_snapshot = {
        f"{scope.value}:{ref}": token for (scope, ref), token in fence.tokens.items()
    }

    return RiskInputs(
        tenant_id=user_id,
        execution_ref=f"exec:{execution_id}",
        certified_badge=None,
        allocated_capital=None,
        intent=intent,
        equity=EquityInputs(
            total_equity=total_equity,
            available_balance=available_balance,
            day_start_equity=day_start_equity,
            peak_equity=peak_equity,
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            as_of=now,
        ),
        exposure=ExposureSnapshot(
            gross_notional=gross_notional,
            net_notional=net_notional,
            open_positions_count=exposure_row.open_positions_count,
            position_quantity=exposure_row.position_quantity,
            symbol_market_value=exposure_row.gross_symbol,
            gross_leverage=exposure_row.max_leverage,
            as_of=now,
        ),
        stats=StatsInputs(
            var_pct=var_result.var_pct if var_result is not None else None,
            es_pct=var_result.es_pct if var_result is not None else None,
            var_method=var_result.method.value if var_result is not None else None,
            lookback_bars=var_result.lookback_bars if var_result is not None else None,
            bars_used=var_result.bars_used if var_result is not None else None,
            correlated_exposure_pct=correlated_exposure_pct,
            max_correlation=max_correlation,
            missing_pairs=missing_pairs,
            as_of=now,
        ),
        activity=ActivityInputs(
            trades_last_1h=exposure_row.trades_1h,
            trades_avg_per_hour_24h=Decimal(exposure_row.trades_24h) / Decimal("24"),
        ),
        safety=SafetyInputs(
            circuit_breaker_level=exposure_row.cb_level,
            active_control_scopes=None,
            fence_snapshot=fence_snapshot,
            data_distrust_level=exposure_row.distrust_level,
            distrust_sources_available=None,
            connection_fresh=None,
            execution_paused_by_safety=exposure_row.paused_by == "SAFETY_LAYER",
            rule_bundle_active=None,
        ),
        limits=(),
        as_of=now,
    )


def to_legacy_dict(inputs: RiskInputs) -> dict[str, Any]:
    """`account_state.py`가 소비하던 14키 평면 dict — R-17
    `RiskEngine._bridge_legacy_inputs`가 그대로 다시 `RiskInputs`로
    복원한다(`from_legacy_dict`). `leverage`는 열린 포지션이 없을 때
    `None`(레버리지 미사용)이 아니라 기존 관례대로 `1`(무레버리지)로
    채운다 — 이 필드만은 "판정 불가"가 아니라 "실제로 알려진 값"이기
    때문이다(포지션이 없으면 레버리지도 없다는 사실 자체가 확정적)."""
    leverage = inputs.exposure.gross_leverage
    return {
        "daily_pnl_pct": inputs.equity.daily_pnl_pct,
        "drawdown_pct": inputs.equity.drawdown_pct,
        "position_quantity": inputs.exposure.position_quantity,
        "total_equity": inputs.equity.total_equity,
        "certified_badge": inputs.certified_badge,
        "allocated_capital": inputs.allocated_capital,
        "available_balance": inputs.equity.available_balance,
        "var_pct": inputs.stats.var_pct,
        "correlated_exposure_pct": inputs.stats.correlated_exposure_pct,
        "recent_trade_count_1h": inputs.activity.trades_last_1h,
        "avg_trade_count_24h": inputs.activity.trades_avg_per_hour_24h,
        "circuit_breaker_level": inputs.safety.circuit_breaker_level,
        "execution_paused_by_safety": inputs.safety.execution_paused_by_safety,
        "leverage": leverage if leverage is not None else Decimal("1"),
    }
