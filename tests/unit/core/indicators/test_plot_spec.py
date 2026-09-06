"""IND-15 — PlotSpec 1급 필드 + output_flags 자동 도출 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11 IND-15
(ADR-2026-09-06-C D2 스키마, ADR-2026-09-06-F D1 도출 규칙).

DoD: 기존 지표 전부 PlotSpec 보유(스냅샷) + 일목구름·볼린저 채움이 스펙만으로
표현됨 + PlotSpec 없는 지표는 등록 거부(fail-closed) + negative(미지 kind/scale,
fill_between 미지 출력 참조 거부).
"""
from __future__ import annotations

import pytest

from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec
from src.core.indicators.specs_talib import TALIB_SPECS, _plot_kind, _plots_from_talib

# --- 스냅샷: 11개 코어 지표 전부 PlotSpec 보유, kind/fill_between이 output_flags와 일치 ---

_EXPECTED_KINDS: dict[str, tuple[str, ...]] = {
    "SMA": ("line",),
    "EMA": ("line",),
    "RSI": ("line",),
    "ATR": ("line",),
    "CCI": ("line",),
    "WILLR": ("line",),
    "MFI": ("line",),
    "MACD": ("line", "line", "histogram"),
    "BBANDS": ("band", "line", "band"),
    "STOCH": ("line", "line"),
    "OBV": ("line",),
}

_EXPECTED_FILL_BETWEEN: dict[str, tuple[str | None, ...]] = {
    "SMA": (None,),
    "EMA": (None,),
    "RSI": (None,),
    "ATR": (None,),
    "CCI": (None,),
    "WILLR": (None,),
    "MFI": (None,),
    "MACD": (None, None, None),
    "BBANDS": ("lowerband", None, "upperband"),
    "STOCH": (None, None),
    "OBV": (None,),
}


def test_every_talib_spec_has_one_plot_per_output() -> None:
    for name, spec in TALIB_SPECS.items():
        assert len(spec.plots) == len(spec.outputs), name
        assert all(isinstance(plot, PlotSpec) for plot in spec.plots)


@pytest.mark.parametrize("name", sorted(TALIB_SPECS))
def test_snapshot_plot_kind_and_fill_between_match_output_flags(name: str) -> None:
    spec = TALIB_SPECS[name]
    assert tuple(plot.kind for plot in spec.plots) == _EXPECTED_KINDS[name]
    assert tuple(plot.fill_between for plot in spec.plots) == _EXPECTED_FILL_BETWEEN[name]


def test_bollinger_fill_is_expressed_purely_by_spec() -> None:
    """볼린저 채움: BBANDS upperband<->lowerband가 스펙만으로 band+fill_between 쌍을 이룬다."""
    spec = TALIB_SPECS["BBANDS"]
    by_output = dict(zip(spec.outputs, spec.plots, strict=True))

    assert by_output["upperband"].kind == "band"
    assert by_output["lowerband"].kind == "band"
    assert by_output["upperband"].fill_between == "lowerband"
    assert by_output["lowerband"].fill_between == "upperband"
    # middleband은 채움에 관여하지 않는 단순 선
    assert by_output["middleband"].fill_between is None


def test_ichimoku_cloud_fill_is_representable_by_spec_type() -> None:
    """일목구름: TA-Lib에 없는 지표라도 PlotSpec.kind="cloud"+fill_between으로
    구름 채움을 스펙만으로 표현할 수 있어야 한다(향후 커스텀 지표 카탈로그가 이 형태를 그대로 쓴다).
    """
    senkou_a = PlotSpec(
        kind="cloud", scale="overlay", default_pane="price", fill_between="senkou_b"
    )
    senkou_b = PlotSpec(
        kind="cloud", scale="overlay", default_pane="price", fill_between="senkou_a"
    )
    spec = IndicatorSpec(
        name="ICHIMOKU_CLOUD",
        inputs=("high", "low"),
        params=(),
        outputs=("senkou_a", "senkou_b"),
        lookback=lambda _params: 0,
        plots=(senkou_a, senkou_b),
    )
    assert spec.plots[0].kind == "cloud"
    assert spec.plots[0].fill_between == "senkou_b"
    assert spec.plots[1].fill_between == "senkou_a"


def test_plots_from_talib_derives_kind_from_output_flags_not_hardcoded() -> None:
    """`_plot_kind`는 output_flags 문자열만 보고 판정한다 — 지표 이름을 몰라도 성립."""
    assert _plot_kind(["Line"]) == "line"
    assert _plot_kind(["Dashed Line"]) == "line"
    assert _plot_kind(["Histogram"]) == "histogram"
    assert _plot_kind(["Values represent an upper limit"]) == "band"
    assert _plot_kind(["Values represent a lower limit"]) == "band"


def test_plots_from_talib_pairs_upper_lower_limit_outputs_automatically() -> None:
    plots = _plots_from_talib(
        "BBANDS",
        ("hi", "mid", "lo"),
        {
            "hi": {"scale": "overlay", "default_pane": "price"},
            "mid": {"scale": "overlay", "default_pane": "price"},
            "lo": {"scale": "overlay", "default_pane": "price"},
        },
    )
    assert plots[0].kind == "band" and plots[0].fill_between == "lo"
    assert plots[1].kind == "line" and plots[1].fill_between is None
    assert plots[2].kind == "band" and plots[2].fill_between == "hi"


# --- negative: fail-closed -----------------------------------------------


def test_plots_from_talib_rejects_output_count_mismatch() -> None:
    with pytest.raises(ValueError, match="output_flags count"):
        _plots_from_talib("SMA", ("value", "extra"), {})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "spline", "scale": "own", "default_pane": "price"},
        {"kind": "line", "scale": "percentile", "default_pane": "price"},
        {"kind": "line", "scale": "own", "default_pane": "subchart"},
    ],
)
def test_plot_spec_rejects_unknown_enum_value(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        PlotSpec(**kwargs)  # type: ignore[arg-type]


def test_plot_spec_rejects_negative_precision() -> None:
    with pytest.raises(ValueError, match="precision"):
        PlotSpec(kind="line", scale="own", default_pane="price", precision=-1)


def test_indicator_spec_rejects_missing_plot_for_an_output() -> None:
    """PlotSpec 없는 지표는 등록 거부(fail-closed) — outputs 1개인데 plots가 0개."""
    with pytest.raises(ValueError, match="PlotSpec count"):
        IndicatorSpec(
            name="NO_PLOT",
            inputs=("close",),
            params=(),
            outputs=("value",),
            lookback=lambda _params: 0,
            plots=(),
        )


def test_indicator_spec_rejects_extra_plot_without_matching_output() -> None:
    with pytest.raises(ValueError, match="PlotSpec count"):
        IndicatorSpec(
            name="TOO_MANY_PLOTS",
            inputs=("close",),
            params=(),
            outputs=("value",),
            lookback=lambda _params: 0,
            plots=(
                PlotSpec(kind="line", scale="own", default_pane="price"),
                PlotSpec(kind="line", scale="own", default_pane="price"),
            ),
        )


def test_indicator_spec_rejects_fill_between_referencing_unknown_output() -> None:
    with pytest.raises(ValueError, match="fill_between references unknown output"):
        IndicatorSpec(
            name="BAD_FILL",
            inputs=("close",),
            params=(),
            outputs=("value",),
            lookback=lambda _params: 0,
            plots=(
                PlotSpec(
                    kind="band", scale="own", default_pane="price", fill_between="does_not_exist"
                ),
            ),
        )


def test_param_spec_and_plot_spec_are_both_frozen() -> None:
    plot = PlotSpec(kind="line", scale="own", default_pane="price")
    with pytest.raises(AttributeError):
        plot.kind = "histogram"  # type: ignore[misc]
    param = ParamSpec(name="timeperiod", min=2, max=500, default=20)
    with pytest.raises(AttributeError):
        param.default = 30  # type: ignore[misc]
