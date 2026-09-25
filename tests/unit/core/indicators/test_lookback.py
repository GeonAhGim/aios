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
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts.check_code_language import count_file
from src.core.indicators.lookback import indicator_required_bars, required_bars
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.strategy.indicator_key import IndicatorKeyError
from tests.conftest import PerfBudget

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


# -- D2 실패 주입 --------------------------------------------------------------


def test_l07_registry_failure_on_one_key_aborts_whole_batch_not_partial() -> None:
    """`required_bars` has exactly one collaborator (`IndicatorRegistry.lookback`);
    inject a registry regression (e.g. a corrupted spec entry) that raises for
    one key in a multi-key, single-timeframe batch and confirm the exception
    propagates instead of `required_bars` silently returning a dict with only
    the keys computed before the failure. A partial dict here would under-size
    the candle fetch for that timeframe (L14 `market_state.required_timeframes`
    consumes this result directly) instead of failing the whole tick closed."""

    class _PoisonedRegistry:
        def lookback(self, name: str, params: Mapping[str, int]) -> int:
            if name == "MACD":
                raise RuntimeError("simulated registry corruption")
            return REGISTRY.lookback(name, params)

    with pytest.raises(RuntimeError):
        required_bars(
            ["SMA_timeperiod20@1h", "MACD_fastperiod12_slowperiod26_signalperiod9@1h"],
            _PoisonedRegistry(),
        )


# -- D2 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
def test_l07_required_bars_throughput_budget(perf_budget: PerfBudget) -> None:
    """`required_timeframes` (L14 `market_state.py`) calls `required_bars`
    once per warm-up sizing pass; 5,000 calls over a 6-key/3-timeframe
    strategy must stay well under a 500ms budget to rule out a pathological
    per-call regression (e.g. re-parsing specs) creeping into this path.
    task-7434: measured via the shared process_time-based perf_budget
    fixture instead of raw wall-clock perf_counter()."""
    keys = [
        "SMA_timeperiod20@1h",
        "RSI_timeperiod14@1h",
        "MACD_fastperiod12_slowperiod26_signalperiod9@4h",
        "SMA_timeperiod50@4h",
        "RSI_timeperiod14@1d",
        "SMA_timeperiod20@1d",
    ]

    def _run_once() -> None:
        for _ in range(5_000):
            required_bars(keys, REGISTRY)

    perf_budget.assert_within(_run_once, budget_ms=500.0, label="5,000 required_bars calls")


# -- D2 게이트 적색 재현 -------------------------------------------------------


def test_l07_code_language_gate_flags_korean_comment_regression(tmp_path: Path) -> None:
    """ADR-2026-09-07-A requires English-only comments/docstrings under `src/`
    (enforced by `scripts/check_code_language.py`, wired into CI). Prove the
    gate's own counter fires red for a synthetic Hangul-comment regression,
    and stays green (0) for this leaf's actual current source -- using a temp
    file so the test doesn't require the regression to exist in the tree."""
    poisoned = tmp_path / "poisoned.py"
    poisoned.write_text(
        "def f() -> int:\n"
        "    # simulated regression comment written in Korean: 이것은 한글 주석\n"
        "    return 1\n",
        encoding="utf-8",
    )
    assert count_file(poisoned) == 1
    assert count_file(LOOKBACK_PATH) == 0
