"""LA-23b — Column-oriented read-only view (ADR-2026-09-04-A #1).

Spec: docs/design/ADR-2026-09-04-A-market-data-replay-perf.md #1,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4.

Provides array containers for ts/o/h/l/c/v so bulk consumers like replay
and backtesting can iterate candle rows without validating/instantiating
each one through the pydantic `CandleRecord`. `close_time` is derivable
at write time from the invariant (`domain/quality/ohlc_sanity.check_candle`
enforces `close_time == open_time + duration(timeframe)`), and violating
candles are quarantined as REJECT and never stored in `md_candle`, so it
is not held in the array.

The ADR originally listed the array as "ts/o/h/l/c/v" only; excluding
`quote_volume` here would prevent `to_candle_records` from losslessly
reconstructing `CandleRecord`, causing `domain/lineage.batch_hash` to
differ from the original value. Adding it is a necessary extension to
satisfy both contracts/v1 immutability (P5) and batch_hash byte
identity (P3 WORM, same ADR #2) — a documented deviation kept openly.

No I/O — holds pure data holders and pure transformation functions only.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["CandleColumns", "MismatchedColumnLengthError", "to_candle_records"]


class MismatchedColumnLengthError(ValueError):
    """`MD_CANDLE_COLUMNS_LENGTH_MISMATCH` — when array lengths differ,
    index-based access silently pairs mismatched rows (reject via fail-closed)."""

    def __init__(self, lengths: dict[str, int]) -> None:
        super().__init__(f"CandleColumns 배열 길이가 서로 다릅니다: {lengths}")


@dataclass(frozen=True, slots=True)
class CandleColumns:
    """Read-only column arrays. Index `i` maps to one candle (`ts[i]` is
    `open_time`). Sort order (`open_time ASC`) is guaranteed by the
    adapter's `ORDER BY` — this type itself does not verify sorting."""

    ts: list[AwareDatetime]
    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    volume: list[Decimal]
    quote_volume: list[Decimal | None]

    def __len__(self) -> int:
        return len(self.ts)


def to_candle_records(columns: CandleColumns, key: SeriesKey) -> list[CandleRecord]:
    """Assumes `columns` was queried filtered by `key` (venue/instrument_id/
    timeframe) (caller's responsibility — port contract, see
    `ports/candle_store.CandleStore.read_candles_columnar`). Skips
    rebuilding `SeriesKey` per row and shares the `key` instance directly —
    the WHERE clause already returns only rows matching that value, so it
    is always identical.

    Skips field validation via `CandleRecord.model_construct` — asyncpg
    already returns the correct types from the DB (NUMERIC→Decimal,
    TIMESTAMPTZ→aware datetime), so re-validation is pure overhead. Safe
    only for data that passed `ohlc_sanity.check_candle` at write time."""
    n = len(columns)
    lengths = {
        "open": len(columns.open),
        "high": len(columns.high),
        "low": len(columns.low),
        "close": len(columns.close),
        "volume": len(columns.volume),
        "quote_volume": len(columns.quote_volume),
    }
    if any(length != n for length in lengths.values()):
        raise MismatchedColumnLengthError({"ts": n, **lengths})

    step = duration(key.timeframe)
    return [
        CandleRecord.model_construct(
            key=key,
            open_time=columns.ts[i],
            close_time=columns.ts[i] + step,
            open=columns.open[i],
            high=columns.high[i],
            low=columns.low[i],
            close=columns.close[i],
            volume=columns.volume[i],
            quote_volume=columns.quote_volume[i],
        )
        for i in range(n)
    ]
