"""FD-9.2/9.5 griefing-defense basket returns (RTF-03, docs/RED_TEAM_FINDINGS.md
2026-09-05-44) — split out of `watchdog_process.py` to stay under the P6
300-line cap (architecture_guard.py `SRC_LINE_CAP`); this module owns nothing
but "how do we source a basket cross-section", the same way
`market_correlation.py` owns nothing but "is a basket move market-wide".

The basket itself is a Bitget spot cross-section one symbol above
`is_market_wide_move`'s `min_symbols=3` default. The spec does not name
specific symbols; these are chosen for liquidity/API stability only.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Protocol

from src.data.models.market_data import Candle

logger = logging.getLogger(__name__)

BASKET_SYMBOLS: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT")
BASKET_RETURN_TIMEFRAME = "5m"
MARKET_WIDE_MOVE_THRESHOLD_PCT = Decimal("3.0")

GetBasketReturnsFn = Callable[[], Awaitable[dict[str, Decimal]]]


class OHLCVSource(Protocol):
    """core-no-io 계약(`.importlinter`) 때문에 `src.exchanges.bitget.adapter.
    BitgetAdapter`를 직접 타입으로 쓸 수 없다 — 이 basket 조회에 실제로 필요한
    메서드 하나만 구조적 타입으로 선언한다. 호출부(`watchdog_process.py`,
    core 밖)는 여전히 `BitgetAdapter` 인스턴스를 그대로 넘긴다."""

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...


async def get_basket_returns(adapter: OHLCVSource) -> dict[str, Decimal]:
    """The latest closed 5m candle's (close-open)/open % move per symbol.

    A symbol whose fetch fails is dropped from the basket rather than defaulted
    to 0% (a fetch failure must not be silently read as "no move" — fail-closed,
    §R3). If too many symbols drop out, `is_market_wide_move` itself falls back
    to None (undecidable) once fewer than `min_symbols` remain."""
    returns: dict[str, Decimal] = {}
    for symbol in BASKET_SYMBOLS:
        try:
            candles = await adapter.get_ohlcv(symbol, BASKET_RETURN_TIMEFRAME, limit=1)
        except Exception:  # noqa: BLE001 — a fetch failure drops the symbol, never fakes 0%
            logger.warning("Watchdog: basket 시세 조회 실패 symbol=%s", symbol, exc_info=True)
            continue
        if not candles or candles[0].open == 0:
            continue
        candle = candles[0]
        returns[symbol] = (candle.close - candle.open) / candle.open * 100
    return returns
