"""R-31 §9 — 레거시 dict 어댑터, to_legacy_dict() 위임만(§2 표 121행)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.inputs import OrderIntent
from src.data.models.market_data import Candle
from src.data.models.trading import AccountBalance
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker as EqTracker
from src.services.execution_loop.risk_inputs_assembler import (
    RiskInputCaches,
    assemble_risk_inputs,
    to_legacy_dict,
)


async def assemble_account_state(
    pool: asyncpg.Pool, *, execution_id: int, user_id: UUID, symbol: str, total_equity: Decimal,
    position_quantity: Decimal, available_balance: Decimal, allocated_capital: Decimal,
    certified_badge: bool, candles: list[Candle], equity_tracker: EqTracker, policy: RiskPolicy,
) -> dict[str, Any]:
    intent = OrderIntent(
        symbol=symbol, asset_class="CRYPTO_SPOT", side="BUY", quantity=position_quantity,
        ref_price=candles[-1].close if candles else Decimal("0"), notional=Decimal("0"),
        reduce_only=position_quantity != 0, strategy_id="", strategy_version="unknown",
        capital_pct=Decimal("0"),
    )
    balances = [AccountBalance(
        exchange=candles[0].exchange if candles else "", asset="USDT",
        total=total_equity, available=available_balance,
    )]
    inputs = await assemble_risk_inputs(
        pool, RiskInputCaches(equity_tracker=equity_tracker), execution_id=execution_id,
        user_id=user_id, intent=intent, balances=balances, candles=candles,
        policy=policy, now=datetime.now(timezone.utc),
    )
    legacy = to_legacy_dict(inputs)
    legacy.update(certified_badge=certified_badge, allocated_capital=allocated_capital,
                  position_quantity=position_quantity)
    return legacy
