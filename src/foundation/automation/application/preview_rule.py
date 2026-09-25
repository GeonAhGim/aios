"""Historical-replay preview (U-4 DoD: "the value recomputed later matches the
value shown at save time (deterministic)"). Reuses `ListBars`
(`foundation/backtest/adapters/list_bars.py`)'s point-in-time access to share
the same principle as the backtest engine (I-05: same domain logic, no
look-ahead) — referencing a future index while walking bars fails immediately
with `BacktestLookaheadError`.

No I/O (pure) — the caller passes in per-symbol bar/indicator/disclosure
sequences it already fetched (the same separation as `quick_backtest.py`
pre-reading `CandleColumns` and passing it in). Assumes indices are aligned
on the same timeframe across symbols (multi-symbol timestamp joining is out
of scope for this leaf).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType

from src.core.risk.hashing import canonical_json, sha256_hex
from src.data.models.market_data import Candle
from src.foundation.automation.contracts.v1 import (
    Condition,
    InvalidRuleDefinitionError,
    PreviewResult,
)
from src.foundation.automation.domain.evaluate import MarketSnapshot, evaluate_conditions
from src.foundation.automation.flags import require_flag_enabled
from src.foundation.backtest.api import ListBars

__all__ = ["preview_rule"]

_EMPTY_INDICATORS: Mapping[str, Sequence[Mapping[str, Decimal]]] = MappingProxyType({})
_EMPTY_DISCLOSURES: Mapping[str, Sequence[frozenset[str]]] = MappingProxyType({})


def _symbol_of(condition: Condition) -> str | None:
    return getattr(condition, "symbol", None)


def _fingerprint(bars_by_symbol: Mapping[str, Sequence[Candle]]) -> str:
    payload = {
        symbol: [
            {
                "open_time": c.open_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in bars
        ]
        for symbol, bars in sorted(bars_by_symbol.items())
    }
    return sha256_hex(canonical_json(payload))


def preview_rule(
    conditions: tuple[Condition, ...],
    bars_by_symbol: Mapping[str, Sequence[Candle]],
    indicators_by_symbol: Mapping[str, Sequence[Mapping[str, Decimal]]] = _EMPTY_INDICATORS,
    disclosures_by_symbol: Mapping[str, Sequence[frozenset[str]]] = _EMPTY_DISCLOSURES,
) -> PreviewResult:
    require_flag_enabled()
    symbols = tuple(sorted({s for s in (_symbol_of(c) for c in conditions) if s is not None}))
    if not symbols:
        raise InvalidRuleDefinitionError(
            "가격·지표·공시 조건이 최소 1개 필요하다(시간 조건만으로는 미리보기 불가)"
        )

    bars = {symbol: ListBars(bars_by_symbol.get(symbol, ())) for symbol in symbols}
    lengths = {len(b) for b in bars.values()}
    if len(lengths) > 1:
        raise InvalidRuleDefinitionError(
            "심볼별 봉 개수가 다르다 — 같은 타임프레임으로 정렬된 데이터가 필요하다"
        )
    length = next(iter(lengths), 0)

    def _snapshot(symbol: str, index: int) -> MarketSnapshot:
        candle = bars[symbol].at(index)
        indicator_series = indicators_by_symbol.get(symbol, ())
        indicators = indicator_series[index] if index < len(indicator_series) else {}
        disclosure_series = disclosures_by_symbol.get(symbol, ())
        disclosures = disclosure_series[index] if index < len(disclosure_series) else frozenset()
        return MarketSnapshot(candle=candle, indicators=indicators, disclosures=disclosures)

    trigger_count = 0
    first_trigger_at = None
    last_trigger_at = None
    for i in range(length):
        snapshots = {symbol: _snapshot(symbol, i) for symbol in symbols}
        prev_snapshots = {symbol: _snapshot(symbol, i - 1) for symbol in symbols} if i > 0 else {}
        if evaluate_conditions(conditions, snapshots, prev_snapshots):
            trigger_count += 1
            ts = bars[symbols[0]].at(i).open_time
            if first_trigger_at is None:
                first_trigger_at = ts
            last_trigger_at = ts

    return PreviewResult(
        trigger_count=trigger_count,
        evaluated_bars=length,
        first_trigger_at=first_trigger_at,
        last_trigger_at=last_trigger_at,
        data_fingerprint=_fingerprint(bars_by_symbol),
    )
