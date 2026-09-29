"""L4_analytics_authoring_backtest_marketplace_v1.0.md#IND-2g — specs_talib catalog generation."""

from __future__ import annotations

from src.core.indicators.specs_talib import TALIB_GROUPS, TALIB_SPECS


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
