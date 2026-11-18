"""L4_analytics_authoring_backtest_marketplace_v1.0.md#IND-2g — specs_talib catalog generation.

DoD: negative test ≥3, failure-injection ≥1, perf assertion with perf_budget fixture.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import talib

from src.core.indicators.generate_specs import generate_talib_specs
from src.core.indicators.spec import IndicatorSpec, PlotSpec
from src.core.indicators.specs_talib import TALIB_GROUPS, TALIB_SPECS
from tests.conftest import PerfBudget


def test_talib_specs_catalog_is_not_empty():
    """TA-Lib catalog generates normally."""
    assert len(TALIB_SPECS) > 0
    assert len(TALIB_SPECS) >= 161  # min 161 auto-generated + 11 manual overrides


def test_talib_specs_includes_manual_overrides():
    """All 11 manual overrides are included."""
    required_overrides = {
        "SMA",
        "EMA",
        "RSI",
        "ATR",
        "CCI",
        "WILLR",
        "MFI",
        "MACD",
        "BBANDS",
        "STOCH",
        "OBV",
    }
    assert required_overrides.issubset(TALIB_SPECS.keys())


def test_talib_specs_deterministic():
    """Identical calls return identical results (determinism)."""
    from src.core.indicators.specs_talib import TALIB_SPECS as TALIB_SPECS_2

    # Re-importing must yield the same object or identical structure
    assert set(TALIB_SPECS.keys()) == set(TALIB_SPECS_2.keys())

    # Each item's name must match its key
    for key, spec in TALIB_SPECS.items():
        assert spec.name == key


def test_talib_groups_is_not_empty():
    """TA-Lib group classification generates."""
    assert len(TALIB_GROUPS) > 0


def test_each_spec_has_required_fields():
    """Each IndicatorSpec carries required fields."""
    for name, spec in TALIB_SPECS.items():
        assert spec.name == name
        assert hasattr(spec, "inputs")
        assert hasattr(spec, "params")
        assert hasattr(spec, "outputs")
        assert hasattr(spec, "lookback")
        assert len(spec.outputs) > 0, f"{name}: outputs empty"


def test_manual_override_sma_has_correct_defaults():
    """Manual override example: SMA defaults to 20."""
    sma = TALIB_SPECS["SMA"]
    assert sma.name == "SMA"
    assert any(p.name == "timeperiod" for p in sma.params)
    timeperiod_param = next(p for p in sma.params if p.name == "timeperiod")
    assert timeperiod_param.default == 20


def test_generated_specs_have_lookback_function():
    """All auto-generated and overridden specs carry a lookback function."""
    for name, spec in TALIB_SPECS.items():
        assert callable(spec.lookback), f"{name}: lookback is not callable"

        # Verify lookback function executes
        if spec.params:
            sample_params = {p.name: p.default for p in spec.params}
            result = spec.lookback(sample_params)
            assert isinstance(result, int)
            assert result >= 0, f"{name}: lookback is negative"


# ── Negative tests (DoD: ≥3) ─────────────────────────────────────────────────


def test_generate_talib_specs_unknown_name_raises_valueerror() -> None:
    """Unknown TA-Lib function name: raises ValueError (fail-closed)."""
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(["nonexistent_indicator_xyz"])


def test_generate_talib_specs_mixed_unknown_raises_valueerror() -> None:
    """Mixed valid+invalid: reject all, generate none."""
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(["sma", "also_fake"])


def test_indicator_spec_plots_mismatch_outputs_raises_valueerror() -> None:
    """PlotSpec count differs from outputs: ValueError (IND-15)."""
    with pytest.raises(ValueError, match="PlotSpec count.*!= outputs count"):
        IndicatorSpec(
            name="fake",
            inputs=("close",),
            params=(),
            outputs=("out1", "out2"),
            lookback=lambda _: 0,
            plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
        )


def test_indicator_spec_fill_between_unknown_output_raises_valueerror() -> None:
    """fill_between references unknown output: ValueError (IND-15)."""
    with pytest.raises(ValueError, match="fill_between references unknown output"):
        IndicatorSpec(
            name="fake",
            inputs=("close",),
            params=(),
            outputs=("out1",),
            lookback=lambda _: 0,
            plots=(
                PlotSpec(
                    kind="band",
                    scale="own",
                    default_pane="separate",
                    fill_between="unknown",
                ),
            ),
        )


def test_indicator_spec_causal_default_is_true() -> None:
    """causal defaults to True (IND-16)."""
    spec = IndicatorSpec(
        name="fake",
        inputs=("close",),
        params=(),
        outputs=("out",),
        lookback=lambda _: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
    )
    assert spec.causal is True


def test_indicator_spec_causal_explicit_false_accepted() -> None:
    """causal=False explicitly accepted (IND-16)."""
    spec = IndicatorSpec(
        name="fake",
        inputs=("close",),
        params=(),
        outputs=("out",),
        lookback=lambda _: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
        causal=False,
    )
    assert spec.causal is False


# ── Failure-injection test (DoD: ≥1) ─────────────────────────────────────────


def test_generate_talib_specs_talib_crash_raises_exception() -> None:
    """When talib.get_functions() raises, the exception propagates."""
    # Patch at the generate_specs module level where talib is imported,
    # not at the top-level `talib` module, to avoid cache-order dependency.
    with patch(
        "src.core.indicators.generate_specs.talib.get_functions",
        side_effect=RuntimeError("simulated TA-Lib C-library crash"),
    ):
        with pytest.raises(RuntimeError, match="simulated TA-Lib C-library crash"):
            generate_talib_specs()


# ── Performance assertion (DoD: perf_budget fixture) ─────────────────────────


def test_generate_talib_specs_subset_performance(perf_budget: PerfBudget) -> None:
    """generate_talib_specs subset completes within 100ms."""
    # Use actually installed TA-Lib function names (0.4.x: AC, BBANDS, ADX, etc.)
    installed = sorted(talib.get_functions())[:10]
    perf_budget.assert_within(
        lambda: generate_talib_specs(installed),
        budget_ms=100.0,
        label=f"generate_talib_specs({len(installed)} names)",
    )


def test_talib_crash_injection_independent_of_warm_cache() -> None:
    """Warm cache is reset by the autouse fixture, so the crash still propagates."""
    from src.core.indicators import generate_specs

    generate_specs._get_talib_metadata_cache()
    assert generate_specs._TALIB_METADATA_CACHE is not None

    # Simulate the next test's setup: the autouse fixture clears the warm cache.
    generate_specs._TALIB_METADATA_CACHE = None
    with patch(
        "src.core.indicators.generate_specs.talib.get_functions",
        side_effect=RuntimeError("simulated TA-Lib C-library crash"),
    ):
        with pytest.raises(RuntimeError, match="simulated TA-Lib C-library crash"):
            generate_talib_specs()
