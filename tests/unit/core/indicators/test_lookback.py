"""L07 tests -- docs/specs/L4_strategy_portfolio_backtest_v1.0.md #section9 L07.

Literal `required_bars` values below are `IndicatorRegistry().lookback(...)`
output + 1, captured at authoring time by running:

    python -c "from src.core.indicators.registry import IndicatorRegistry as R; \
        r = R(); print(r.lookback('SMA', {'timeperiod': 20}), \
        r.lookback('SMA', {'timeperiod': 50}), r.lookback('RSI', {'timeperiod': 14}), \
        r.lookback('MACD', {'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9}))"

Output: `19 49 14 33` -> +1 each: SMA(20)=20, SMA(50)=50, RSI(14)=15, MACD(default)=34.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.indicators.lookback import indicator_required_bars, required_bars
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.strategy.indicator_key import IndicatorKeyError

REGISTRY = IndicatorRegistry()
LOOKBACK_PATH = Path(__file__).resolve().parents[4] / "src/core/indicators/lookback.py"

LITERAL_CASES: tuple[tuple[str, int], ...] = (
    ("SMA_timeperiod20@1h", 20),
    ("SMA_timeperiod20@4h", 20),
    ("RSI_timeperiod14@1h", 15),
    ("RSI_timeperiod14@4h", 15),
    ("MACD_fastperiod12_slowperiod26_signalperiod9@1h", 34),
    ("MACD_fastperiod12_slowperiod26_signalperiod9@4h", 34),
)


@pytest.mark.parametrize("key,expected", LITERAL_CASES)
def test_l07_required_bars_literal_values_per_indicator_and_timeframe(
    key: str, expected: int
) -> None:
    tf = key.rsplit("@", 1)[1]
    assert required_bars([key], REGISTRY) == {tf: expected}


def test_l07_required_bars_increases_when_period_increases() -> None:
    low = indicator_required_bars("SMA", {"timeperiod": 20}, REGISTRY)
    high = indicator_required_bars("SMA", {"timeperiod": 50}, REGISTRY)
    assert high > low


def test_l07_required_bars_aggregates_same_timeframe_by_max_not_sum() -> None:
    result = required_bars(
        ["SMA_timeperiod20@1h", "MACD_fastperiod12_slowperiod26_signalperiod9@1h"],
        REGISTRY,
    )
    assert result == {"1h": 34}  # max(20, 34), not 20 + 34 == 54


def test_l07_unknown_indicator_rejected_fail_closed() -> None:
    with pytest.raises(IndicatorError) as excinfo:
        indicator_required_bars("NOT_AN_INDICATOR", {}, REGISTRY)
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_l07_negative_period_and_malformed_key_rejected() -> None:
    with pytest.raises(IndicatorError) as excinfo:
        indicator_required_bars("SMA", {"timeperiod": -5}, REGISTRY)
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"
    with pytest.raises(IndicatorKeyError):
        required_bars(["not a valid key!!"], REGISTRY)


def test_l07_key_without_timeframe_is_rejected_not_defaulted() -> None:
    with pytest.raises(ValueError):
        required_bars(["SMA_timeperiod20"], REGISTRY)


def test_l07_lookback_module_delegates_to_registry_not_talib() -> None:
    """(a) DoD: no talib import, at least one registry import."""
    tree = ast.parse(LOOKBACK_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not any("talib" in name.lower() for name in imports)
    assert any("registry" in name.lower() for name in imports)


def test_l07_lookback_module_has_no_io_or_nondeterministic_calls() -> None:
    """(h) DoD: no asyncpg/httpx/openai import, no datetime.now/random use."""
    source = LOOKBACK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    banned = {"asyncpg", "httpx", "openai", "datetime", "random"}
    assert not (imports & banned), f"banned import(s) found: {imports & banned}"
    assert ".now(" not in source
    assert "random" not in source.lower()


def test_l07_lookback_module_is_within_80_line_budget() -> None:
    lines = LOOKBACK_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 80
