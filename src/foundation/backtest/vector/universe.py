"""BT-17 — `backtest/vector/universe.py`: multi-symbol universe sweep.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-17. Depends on: BT-15 (`vector/{arrays,signals,fills}.py`, 1c5e4b5f lineage) · DC-13
(hot/warm tiering, 1212 lineage) — both already merged into origin/main (task-2372 decision).

This module only loops over BT-15b `fills.run_vector_backtest` (vector signal + BT-2~6
event fill engine) per symbol — it does not reimplement the fill arithmetic (I-05).
It also reuses LA-23b/BT-15a `CandleColumns` as-is for candle representation — it does
not introduce a new per-symbol time-series representation such as a dict-of-DataFrame
(§C avoid duplicate context). The universe itself is represented only as a
`symbol -> (CandleColumns, VectorSignal)` mapping.

Memory cap (`MAX_UNIVERSE_CANDLES`): if the sum of candle counts across every symbol in
the universe exceeds the cap, no symbol is processed at all and the call is rejected
immediately (fail-closed) — it never returns a "partial result" that stopped partway
through after processing up to the cap. Because the sum check finishes before the loop
starts, this guarantee comes from the code structure itself (it is not a post-hoc check).
40,000 was chosen as a size that comfortably fits 100 symbols × 1 year of D1 (≈36,500
candles) while still being large enough that a boundary test can actually build that many
`CandleColumns` without strain — this number itself is not derived from measured memory
profiling but exists to demonstrate the structure "if the sum exceeds the cap, computation
never starts" (unverified: the relationship to actually available memory in a real
production environment still needs separate confirmation).

Missing symbols: if a symbol's `CandleColumns` is empty (no candles in the range), that
symbol is not silently skipped — its name is left in `UniverseSweepResult.skipped` as-is —
so callers can distinguish "the sweep succeeded with 0 results" from "only that symbol had
no data".

Pure module — no I/O.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "MAX_UNIVERSE_CANDLES",
    "UniverseMemoryLimitError",
    "UniverseSweepResult",
    "sweep_universe",
]

# Cap on the sum of candle counts across all symbols in the universe — exposed as a
# module constant so callers can check universe size upfront (rationale in the module docstring).
MAX_UNIVERSE_CANDLES = 40_000


class UniverseMemoryLimitError(ValueError):
    """`BT_VECTOR_UNIVERSE_MEMORY_LIMIT` — rejected fail-closed with no partial result
    when the universe's total candle count exceeds `MAX_UNIVERSE_CANDLES`."""

    def __init__(self, total_candles: int) -> None:
        super().__init__(
            f"유니버스 총 캔들 수 {total_candles}가 상한 {MAX_UNIVERSE_CANDLES}를 넘는다 — "
            "부분 결과를 만들지 않고 거부한다"
        )


@dataclass(frozen=True, slots=True)
class UniverseSweepResult:
    """`results` holds only the symbols actually swept (`symbol -> QuickBacktestResult`).
    `skipped` holds symbol names skipped because the range had no candles — the two
    collections always exactly partition the full set of universe keys (no overlap,
    union equals all input keys)."""

    results: dict[str, QuickBacktestResult]
    skipped: tuple[str, ...]


def sweep_universe(
    universe: Mapping[str, tuple[CandleColumns, VectorSignal]],
    config: BacktestConfigV2,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> UniverseSweepResult:
    """Runs `run_vector_backtest` with the same `config` for each symbol in `universe`.

    Because the cap check finishes before the loop starts, a universe that exceeds the
    cap is rejected without computing any symbol (no partial result)."""
    total_candles = sum(len(columns) for columns, _ in universe.values())
    if total_candles > MAX_UNIVERSE_CANDLES:
        raise UniverseMemoryLimitError(total_candles)

    results: dict[str, QuickBacktestResult] = {}
    skipped: list[str] = []
    for symbol, (columns, signal) in universe.items():
        if len(columns) == 0:
            skipped.append(symbol)
            continue
        results[symbol] = run_vector_backtest(
            config, columns, signal, timeframe=timeframe,
            initial_cash=initial_cash, funding_rate=funding_rate,
        )
    return UniverseSweepResult(results=results, skipped=tuple(skipped))
