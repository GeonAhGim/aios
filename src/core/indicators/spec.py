"""L01 — 지표 스펙 타입.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11 IND-15
(ADR-2026-09-06-C D2, ADR-2026-09-06-F D1).

순수 데이터/타입 모듈 — I/O·계산 로직 없음. `IND-1`(지표 엔진)·L07(lookback)·
L28(series_cache)이 이 계약에 1:1 의존하므로 여기 정의한 필드 밖으로
임의 확장하지 않는다. CH-15(프론트 렌더러)가 `PlotSpec`을 SSOT로 파싱만
하므로 필드명을 바꾸면 프론트가 깨진다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

REGISTRY_VERSION = "ind-v1"

PlotKind = Literal["line", "histogram", "area", "band", "cloud", "marker"]
ScaleHint = Literal["own", "overlay", "percent", "log", "inverted"]
DefaultPane = Literal["price", "separate"]

_VALID_PLOT_KINDS = frozenset(get_args(PlotKind))
_VALID_SCALE_HINTS = frozenset(get_args(ScaleHint))
_VALID_DEFAULT_PANES = frozenset(get_args(DefaultPane))


@dataclass(frozen=True)
class ParamSpec:
    """지표 파라미터 하나의 이름과 허용 범위."""

    name: str
    min: int
    max: int
    default: int


@dataclass(frozen=True)
class PlotSpec:
    """지표 출력 하나를 어떻게 그릴지의 선언적 표시 계약(ADR-2026-09-06-C D2).

    `kind`/`fill_between`의 기본값은 손으로 적지 않고 TA-Lib `output_flags`에서
    도출한다(ADR-2026-09-06-F D1) — 도출 로직은 `specs_talib.py`에 있다.
    이 클래스 자체는 도출된 값을 담는 순수 데이터 컨테이너이고, `__post_init__`은
    등록 거부(fail-closed)를 위한 값 검증만 한다.
    """

    kind: PlotKind
    scale: ScaleHint
    default_pane: DefaultPane
    fill_between: str | None = None
    color_rule: str | None = None
    precision: int | None = None
    legend_format: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_PLOT_KINDS:
            raise ValueError(f"unknown PlotSpec.kind: {self.kind!r}")
        if self.scale not in _VALID_SCALE_HINTS:
            raise ValueError(f"unknown PlotSpec.scale: {self.scale!r}")
        if self.default_pane not in _VALID_DEFAULT_PANES:
            raise ValueError(f"unknown PlotSpec.default_pane: {self.default_pane!r}")
        if self.precision is not None and self.precision < 0:
            raise ValueError(f"PlotSpec.precision must be >= 0: {self.precision!r}")


@dataclass(frozen=True)
class IndicatorSpec:
    """지표 하나의 계산 계약: 입력 시리즈, 파라미터, 출력, lookback, 표시 계약.

    `plots`는 `outputs`와 1:1 대응하는 필수 필드다 — 표시 계약(`PlotSpec`) 없이
    지표를 등록할 수 없다(fail-closed, IND-15 DoD).
    """

    name: str
    inputs: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    outputs: tuple[str, ...]
    lookback: Callable[[dict[str, int]], int]
    plots: tuple[PlotSpec, ...]
    causal: bool = True

    def __post_init__(self) -> None:
        if len(self.plots) != len(self.outputs):
            raise ValueError(
                f"{self.name}: PlotSpec count ({len(self.plots)}) "
                f"!= outputs count ({len(self.outputs)})"
            )
        for plot in self.plots:
            if plot.fill_between is not None and plot.fill_between not in self.outputs:
                raise ValueError(
                    f"{self.name}: fill_between references unknown output "
                    f"{plot.fill_between!r} (outputs={self.outputs!r})"
                )
