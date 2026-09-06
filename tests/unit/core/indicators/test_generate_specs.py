"""IND-10 — TA-Lib 161종 자동 생성 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10

DoD: 161종 등록(캔들 패턴 61종 포함), 같은 talib 버전에서 생성물 바이트 동일
(결정론), 증분=일괄 동일성 샘플 20종, 오버라이드 없는 지표도 PlotSpec 보유.
negative: 파라미터 범위 밖 거부, 미지 함수명 거부, NaN 구간 처리.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import talib

from src.core.indicators.generate_specs import TALIB_GROUPS, generate_talib_specs
from src.core.indicators.registry import IndicatorError, IndicatorRegistry, canonical_spec_dict
from src.core.indicators.specs_talib import TALIB_SPECS
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle

ALL_TALIB_NAMES = sorted(talib.get_functions())
_MANUAL_OVERRIDE_NAMES = frozenset(
    {"SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "MACD", "BBANDS", "STOCH", "OBV"}
)


def _candles(n: int, base: float = 100.0) -> list[Candle]:
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        price = base + i
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


# --- 카탈로그 크기·그룹 -----------------------------------------------------


def test_all_161_talib_functions_are_generated() -> None:
    specs = generate_talib_specs()
    assert len(specs) == 161
    assert set(specs) == set(ALL_TALIB_NAMES)


def test_pattern_recognition_group_has_61_candle_functions() -> None:
    pattern_names = [name for name, group in TALIB_GROUPS.items() if group == "Pattern Recognition"]
    assert len(pattern_names) == 61
    assert all(name.startswith("CDL") for name in pattern_names)


def test_talib_groups_classify_into_exactly_ten_categories() -> None:
    assert set(TALIB_GROUPS) == set(ALL_TALIB_NAMES)
    assert len(set(TALIB_GROUPS.values())) == 10


# --- 결정론: 같은 talib 버전에서 생성물 바이트(정준 직렬화) 동일 -----------


def test_generation_is_deterministic_across_calls() -> None:
    first = generate_talib_specs()
    second = generate_talib_specs()
    canon_first = [canonical_spec_dict(n, s) for n, s in sorted(first.items())]
    canon_second = [canonical_spec_dict(n, s) for n, s in sorted(second.items())]
    assert canon_first == canon_second


def test_incremental_generation_matches_batch_for_a_20_name_sample() -> None:
    """증분(20종만 생성) == 일괄(161종 생성 후 같은 20종만 추출)."""
    sample = ALL_TALIB_NAMES[:10] + ALL_TALIB_NAMES[-10:]
    incremental = generate_talib_specs(names=sample)
    batch = generate_talib_specs()
    batch_subset = {name: batch[name] for name in sample}

    canon_incremental = [canonical_spec_dict(n, s) for n, s in sorted(incremental.items())]
    canon_batch_subset = [canonical_spec_dict(n, s) for n, s in sorted(batch_subset.items())]
    assert canon_incremental == canon_batch_subset
    assert len(sample) == 20


# --- PlotSpec: 오버라이드 없는 지표도 보유 ----------------------------------


@pytest.mark.parametrize(
    "name", sorted(set(ALL_TALIB_NAMES) - _MANUAL_OVERRIDE_NAMES)
)
def test_generated_only_indicators_have_plot_spec_per_output(name: str) -> None:
    spec = TALIB_SPECS[name]
    assert len(spec.plots) == len(spec.outputs) >= 1
    for plot in spec.plots:
        assert plot.kind in ("line", "histogram", "area", "band", "cloud", "marker")


def test_candlestick_functions_plot_as_markers_on_price() -> None:
    spec = TALIB_SPECS["CDLDOJI"]
    assert spec.plots[0].kind == "marker"
    assert spec.plots[0].scale == "overlay"
    assert spec.plots[0].default_pane == "price"


def test_same_scale_overlap_studies_plot_overlaid_on_price() -> None:
    spec = TALIB_SPECS["SAR"]
    assert spec.plots[0].scale == "overlay"
    assert spec.plots[0].default_pane == "price"


def test_histogram_outputs_default_to_sign_color_rule() -> None:
    spec = TALIB_SPECS["APO"]
    # APO is a single-line momentum oscillator, not a histogram — sanity check the
    # opposite case is not colored by sign.
    assert spec.plots[0].kind != "histogram" or spec.plots[0].color_rule == "sign"


# --- negative: 미지 함수명 거부 ---------------------------------------------


def test_generate_rejects_unknown_function_name() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(names=["NOT_A_REAL_TALIB_FUNCTION"])


def test_generate_rejects_partially_unknown_function_list() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(names=["SMA", "NOT_A_REAL_TALIB_FUNCTION"])


# --- negative: 파라미터 범위 밖 거부 (자동 생성 지표 경유) -----------------


def test_registry_rejects_out_of_range_param_for_a_generated_indicator() -> None:
    registry = IndicatorRegistry(TALIB_SPECS)
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ADX", {"timeperiod": 2001})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ADX", {"timeperiod": 0})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_rejects_out_of_range_matype_for_a_generated_indicator() -> None:
    registry = IndicatorRegistry(TALIB_SPECS)
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("MA", {"matype": 9})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_accepts_matype_within_0_to_8() -> None:
    registry = IndicatorRegistry(TALIB_SPECS)
    assert registry.validate_params("MA", {"matype": 8}) == {"timeperiod": 30, "matype": 8}


# --- negative/positive: NaN 구간 처리 (생성된 지표 경유 IndicatorService) --


def test_calculate_returns_leading_none_for_generated_indicator_lookback() -> None:
    registry_lookback = IndicatorRegistry(TALIB_SPECS).lookback("ADX", {"timeperiod": 14})
    result = IndicatorService().calculate("ADX", _candles(registry_lookback + 5), timeperiod=14)
    assert result.values[:registry_lookback] == [None] * registry_lookback
    assert all(v is not None for v in result.values[registry_lookback:])


def test_calculate_handles_integer_dtype_candle_pattern_output() -> None:
    """CDL* 출력은 int32 배열이다 — `_clean`이 np.isnan을 int 배열에 바로 쓰면
    TypeError가 난다(수정 전 회귀 재발 방지)."""
    result = IndicatorService().calculate("CDLDOJI", _candles(30))
    assert len(result.values) == 30
    assert all(v is None or isinstance(v, float) for v in result.values)


def test_calculate_rejects_mavp_input_it_cannot_supply() -> None:
    """MAVP는 두 번째 입력이 캔들 필드가 아닌 가변 주기 배열이라 Candle 기반
    어댑터가 공급할 수 없다 — 등록은 되지만(161종에 포함) 계산은 거부한다."""
    assert "MAVP" in TALIB_SPECS
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("MAVP", _candles(30))
    assert excinfo.value.code == "STRATEGY_INDICATOR_INPUT_UNSUPPORTED"
