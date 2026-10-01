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
    """TA-Lib 카탈로그가 정상 생성됨."""
    assert len(TALIB_SPECS) > 0
    assert len(TALIB_SPECS) >= 161  # 최소 161개 자동생성 + 11개 수동 오버라이드


def test_talib_specs_includes_manual_overrides():
    """11개 수동 오버라이드가 모두 포함됨."""
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
    """동일한 호출이 동일한 결과를 반환 (결정성)."""
    from src.core.indicators.specs_talib import TALIB_SPECS as TALIB_SPECS_2

    # 다시 import해도 동일한 객체가 반환되거나, 동일한 구조를 가져야 함
    assert set(TALIB_SPECS.keys()) == set(TALIB_SPECS_2.keys())

    # 각 항목의 name이 키와 일치
    for key, spec in TALIB_SPECS.items():
        assert spec.name == key


def test_talib_groups_is_not_empty():
    """TA-Lib 그룹 분류가 생성됨."""
    assert len(TALIB_GROUPS) > 0


def test_each_spec_has_required_fields():
    """각 IndicatorSpec이 필수 필드를 가짐."""
    for name, spec in TALIB_SPECS.items():
        assert spec.name == name
        assert hasattr(spec, "inputs")
        assert hasattr(spec, "params")
        assert hasattr(spec, "outputs")
        assert hasattr(spec, "lookback")
        assert len(spec.outputs) > 0, f"{name}: outputs 비어있음"


def test_manual_override_sma_has_correct_defaults():
    """수동 오버라이드 예: SMA는 기본값 20을 가짐."""
    sma = TALIB_SPECS["SMA"]
    assert sma.name == "SMA"
    assert any(p.name == "timeperiod" for p in sma.params)
    timeperiod_param = next(p for p in sma.params if p.name == "timeperiod")
    assert timeperiod_param.default == 20


def test_generated_specs_have_lookback_function():
    """자동생성 및 오버라이드 모든 spec이 lookback 함수를 가짐."""
    for name, spec in TALIB_SPECS.items():
        assert callable(spec.lookback), f"{name}: lookback이 callable이 아님"

        # lookback 함수가 동작
        if spec.params:
            sample_params = {p.name: p.default for p in spec.params}
            result = spec.lookback(sample_params)
            assert isinstance(result, int)
            assert result >= 0, f"{name}: lookback이 음수"


# ── Negative tests (DoD: ≥3 건) ──────────────────────────────────────────────


def test_generate_talib_specs_unknown_name_raises_valueerror() -> None:
    """unknown TA-Lib 함수명: ValueError 발생 (fail-closed)."""
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(["nonexistent_indicator_xyz"])


def test_generate_talib_specs_mixed_unknown_raises_valueerror() -> None:
    """유효+무효 혼합: 부분 생성 없이 전체 거부."""
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(["sma", "also_fake"])


def test_indicator_spec_plots_mismatch_outputs_raises_valueerror() -> None:
    """plots 수와 outputs 수가 다르면 ValueError (IND-15)."""
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
    """fill_between이 알려지지 않은 output를 참조하면 ValueError (IND-15)."""
    with pytest.raises(ValueError, match="fill_between.*not in outputs"):
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
    """causal 미지정 시 True (IND-16)."""
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
    """causal=False 명시 허용 (IND-16)."""
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


# ── Failure-injection test (DoD: ≥1 건) ──────────────────────────────────────


def test_generate_talib_specs_talib_crash_raises_exception() -> None:
    """talib.get_functions()가 예외를 raise하면 그 예외가 전파된다."""
    with patch.object(
        talib, "get_functions", side_effect=RuntimeError("simulated TA-Lib C-library crash")
    ):
        with pytest.raises(RuntimeError, match="simulated TA-Lib C-library crash"):
            generate_talib_specs()


# ── Performance assertion (DoD: perf_budget fixture 사용) ────────────────────


def test_generate_talib_specs_subset_performance(perf_budget: PerfBudget) -> None:
    """generate_talib_specs subset 호출은 100ms 이내에 완료된다."""
    # 실제 설치된 TA-Lib 함수명 사용 (0.4.x: AC, BBANDS, ADX 등)
    installed = sorted(talib.get_functions())[:10]
    perf_budget.assert_within(
        lambda: generate_talib_specs(installed),
        budget_ms=100.0,
        label=f"generate_talib_specs({len(installed)} names)",
    )
