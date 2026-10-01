"""R-31 `risk_inputs_assembler.py` 통합테스트 공용 fixture/helper.

`test_risk_inputs_assembler.py`와 `test_risk_inputs_assembler_deepen.py`
(task-10539, loc_over_500 분리)가 공유한다 — 파일마다 같은 helper를
복제하면 `_candles`/`_intent` 등의 고정값이 서로 갈릴 위험이 있어 한 곳에
둔다(CLAUDE.md §7과 동일 취지, `tests/integration/foundation/positions/
_compute_daily_nav_fixtures.py`와 같은 패턴).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.inputs import OrderIntent
from src.data.models.market_data import Candle
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.risk_inputs_assembler import RiskInputCaches

POLICY = load_risk_policy()
NOW = datetime.now(timezone.utc)


def asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def create_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str = "bitget") -> int:
    strategy_id = f"risk-inputs-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies (strategy_id, version, owner_user_id, target_asset, market, "
            "exchange, fsm_definition, author_agent, lifecycle_status) VALUES "
            "($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb, 'test-author', 'APPROVED')",
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            "INSERT INTO strategy_executions (strategy_id, strategy_version, user_id, exchange, "
            "mode, allocated_capital, currency, status) VALUES "
            "($1, '1.0.0', $2, $3, 'PAPER', 1000, 'USDT', 'RUNNING') RETURNING id",
            strategy_id,
            user_id,
            exchange,
        )
    assert row is not None
    return row["id"]


async def insert_position(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    symbol: str,
    exchange: str,
    strategy_id: str,
    quantity: Decimal,
    average_entry_price: Decimal,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) VALUES ($1, $2, $3, $4, $5, $6, now())",
            user_id,
            symbol,
            exchange,
            strategy_id,
            quantity,
            average_entry_price,
        )


def candles(symbol: str, exchange: str, n: int) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol=symbol,
            exchange=exchange,
            timeframe="1d",
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
            open_time=base + timedelta(days=i),
            close_time=base + timedelta(days=i, hours=1),
        )
        for i in range(n)
    ]


def intent(symbol: str, strategy_id: str) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        asset_class="CRYPTO_SPOT",
        side="BUY",
        quantity=Decimal("1"),
        ref_price=Decimal("100"),
        notional=Decimal("100"),
        reduce_only=False,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        capital_pct=Decimal("10"),
    )


def caches() -> RiskInputCaches:
    return RiskInputCaches(equity_tracker=ExecutionEquityTracker(today=lambda: date(2026, 1, 1)))
