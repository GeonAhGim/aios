"""L25 -- input bar snapshot hash: the same bar sequence + source + as_of
always yields the same `snapshot_hash` (R1 reproducibility). `BarSnapshotRef`
pins that hash together with symbol, exchange, timeframe, range, bar count,
source and the as-of query time.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2.4
(`domain/snapshot.py` row). Canonicalizes the same field set as
`validation.domain.rules._bar_fingerprint`, but this module is now the
single source of that hash computation (a future leaf will make `rules.py`
import from here instead).

Normalization reuses R-01 `src.core.risk.hashing.canonical_json` --
`Decimal.normalize()` absorbs trailing-zero representation differences,
and that utility already rejects naive datetimes (no new hash recipe is
invented here).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from src.core.risk.hashing import canonical_json, sha256_hex
from src.data.models.market_data import Candle

HASH_SCHEMA = "backtest-bar-snapshot-1"


class BarSnapshotRef(BaseModel):
    """Every required field is declared without a default -- if any part of
    the snapshot identity (symbol, exchange, timeframe, range, count,
    source, query time) is missing, "what this data actually was" cannot be
    reproduced, so it is rejected with an exception instead of a silent
    default."""

    snapshot_hash: str
    symbol: str
    exchange: str
    timeframe: str
    from_time: datetime
    to_time: datetime
    bar_count: int
    source: str
    as_of: datetime


def _bar_fingerprint(bar: Candle) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "exchange": bar.exchange,
        "timeframe": bar.timeframe,
        "open_time": bar.open_time,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def compute_bar_snapshot_hash(
    bars: Sequence[Candle], *, source: str, as_of: datetime
) -> str:
    """bar sequence + source + as-of time -> sha256 hex (64 chars). Same
    values yield the same hash; a single field change on a single bar
    yields a different one (R1)."""
    if not bars:
        raise ValueError("compute_bar_snapshot_hash: bars가 비어 있습니다")
    if not source:
        raise ValueError("compute_bar_snapshot_hash: source가 비어 있습니다")
    payload = {
        "schema": HASH_SCHEMA,
        "source": source,
        "as_of": as_of,
        "bars": [_bar_fingerprint(b) for b in bars],
    }
    return sha256_hex(canonical_json(payload))


__all__ = ["HASH_SCHEMA", "BarSnapshotRef", "compute_bar_snapshot_hash"]
