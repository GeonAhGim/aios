"""IND-11 -- pandas-ta-classic bridge: TA-Lib 밖 순증분 지표만 등록.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-11,
ADR-2026-09-09-A (명세 원안 pandas-ta 0.4.71b0은 Python>=3.12·저장소 404·numba
고정으로 탈락, 대체 패키지 `pandas-ta-classic`(MIT, `xgboosted/pandas-ta-classic`)
채택), docs/design/INDICATOR_OSS_EVAL.md §5·§6.

This module registers, as `IndicatorSpec`, only those entries of the ~190-name
`pandas-ta-classic` catalog that the installed TA-Lib does not already provide
(`specs_talib.TALIB_SPECS`; 161 names on TA-Lib 0.4.x, 201 on 0.6.x) --
ADR-2026-09-06-F: never keep two computation paths for one indicator, so
anything TA-Lib already computes is not re-registered here.

Overlap detection is name based: a pandas-ta-classic lowercase name upper-cased
that equals an installed TA-Lib function name is an overlap; otherwise
`ALIAS_TO_TALIB` (known synonyms where TA-Lib uses a different spelling) is
consulted. 이것은 ~190종 전체에 대한 완전한
의미론적 동치 검증이 아니다 -- 이름도 안 겹치고 별칭 표에도 없는 지표는
순증분으로 분류한다(안전한 방향: 놓친 중복은 카탈로그 항목 하나가 여분으로
남을 뿐이지만, 놓친 순증분을 중복으로 오판하면 실제로 있는 계산 경로를 조용히
숨기게 된다). `oracle` extra(`tulipy`, LGPL-3.0)는 설치하지 않는다(§5) --
`pyproject.toml`에는 기본 의존성만 선언한다.

순증분 카탈로그(`NET_INCREMENTAL_NAMES`, 현재 268종 중 TA-Lib과 안 겹치는
158종 수준 -- 정확한 수는 `report_net_incremental_count()`가 실행 시점 라이브러리
버전으로 계산해 보고한다) 중 실제로 `IndicatorSpec`을 등록해 계산 가능하게
만든 후보는 `CANDIDATE_INDICATORS`의 3종(DPO, MASSI, COPPOCK)이다.
Candidates whose name the installed TA-Lib provides are classified as
`SUPERSEDED_BY_TALIB` and not registered (TA-Lib 0.6.x ships all three, so on
that version `REGISTERED_INDICATORS` is empty and TA-Lib is the only path);
which side wins is decided by `talib.get_functions()` at import -- this module
holds no TA-Lib version literal. 이 3종은
전부 (a) 단일 출력 (b) 순수 OHLC 입력 (c) look-ahead 없는 인과적(causal) 계산
이라는 세 조건을 모두 만족해 IND-7g 방식 3자 교차검증(§ tests/*)이 성립하는
지표로 선택했다 -- 나머지 순증분 지표(ichimoku/vwap/kc 등 §2.2 후속 확장
대상)는 다중 출력·비인과적(forward-shift) 계산이 섞여 있어 이번 leaf 범위
밖이다(§9.9 DoD는 "순증분 종수 보고"이지 "전량 등록"이 아니다).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
import pandas_ta_classic as pta
from pydantic import BaseModel

from src.core.indicators.adapters.pandas_ta_candidates import (
    CALCULATORS,
    CANDIDATE_INDICATORS,
    CANDIDATE_SPECS,
)
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS
from src.data.models.market_data import Candle

__all__ = [
    "ALIAS_TO_TALIB",
    "ALL_PANDAS_TA_NAMES",
    "CANDIDATE_INDICATORS",
    "CANDIDATE_REGISTRY",
    "CANDIDATE_SPECS",
    "NET_INCREMENTAL_NAMES",
    "PANDAS_TA_REGISTRY",
    "PANDAS_TA_SPECS",
    "REGISTERED_INDICATORS",
    "SUPERSEDED_BY_TALIB",
    "PandasTaBridgeService",
    "PandasTaResult",
    "is_talib_overlap",
    "report_net_incremental_count",
    "select_registered",
]

_TALIB_NAMES: frozenset[str] = frozenset(TALIB_SPECS)

# name(pandas-ta-classic, lowercase) -> TA-Lib name, for cases where the two
# libraries compute the same thing under different spellings. Built from a
# manual read of both catalogs (2026-09-23) -- see module docstring for the
# fail-open rationale on names not covered here.
ALIAS_TO_TALIB: dict[str, str] = {
    "true_range": "TRANGE",
    "wcp": "WCLPRICE",
    "linreg": "LINEARREG",
    "linregangle": "LINEARREG_ANGLE",
    "linregintercept": "LINEARREG_INTERCEPT",
    "linregslope": "LINEARREG_SLOPE",
    "psar": "SAR",
}


def is_talib_overlap(name: str) -> bool:
    """True if `name` (pandas-ta-classic catalog entry) already has a TA-Lib
    computation path -- see module docstring for the name-based method."""
    if name.upper() in _TALIB_NAMES:
        return True
    aliased = ALIAS_TO_TALIB.get(name)
    return aliased is not None and aliased in _TALIB_NAMES


def _all_pandas_ta_names() -> frozenset[str]:
    names: set[str] = set()
    for group_names in pta.Category.values():
        names.update(group_names)
    return frozenset(names)


ALL_PANDAS_TA_NAMES: frozenset[str] = _all_pandas_ta_names()
NET_INCREMENTAL_NAMES: frozenset[str] = frozenset(
    name for name in ALL_PANDAS_TA_NAMES if not is_talib_overlap(name)
)


def report_net_incremental_count() -> dict[str, int]:
    """DoD "순증분 종수 보고, 중복 0" -- 실행 시점 pandas-ta-classic 카탈로그
    기준 집계. `overlap`은 이름 기반 판정으로 걸러진 것만 세므로, 등록된
    3종(`REGISTERED_INDICATORS`)의 중복은 이 카운트로는 0임을 직접 보장하지
    않는다 -- `REGISTERED_INDICATORS`가 전부 `NET_INCREMENTAL_NAMES`의
    부분집합이라는 것은 `test_pandas_ta_bridge.py`가 별도로 단언한다."""
    total = len(ALL_PANDAS_TA_NAMES)
    net = len(NET_INCREMENTAL_NAMES)
    return {"total": total, "net_incremental": net, "overlap": total - net}


def select_registered(
    candidates: Mapping[str, IndicatorSpec], net_incremental: frozenset[str]
) -> tuple[dict[str, IndicatorSpec], tuple[str, ...]]:
    """Split candidates into (registered, superseded_by_talib).

    A candidate is registered only while it is net-incremental against the
    installed TA-Lib (ADR-2026-09-06-F: one computation path per indicator).
    Pure function so the split can be tested against any TA-Lib name set
    without reinstalling the library.
    """
    registered = {name: spec for name, spec in candidates.items() if name in net_incremental}
    superseded = tuple(sorted(set(candidates) - set(registered)))
    return registered, superseded


PANDAS_TA_SPECS, SUPERSEDED_BY_TALIB = select_registered(CANDIDATE_SPECS, NET_INCREMENTAL_NAMES)
REGISTERED_INDICATORS: tuple[str, ...] = tuple(sorted(PANDAS_TA_SPECS))

PANDAS_TA_REGISTRY = IndicatorRegistry(PANDAS_TA_SPECS)
# Every candidate, regardless of the installed TA-Lib -- lets the formula
# cross-verification tests exercise a calculator even where the default
# service refuses it as superseded.
CANDIDATE_REGISTRY = IndicatorRegistry(CANDIDATE_SPECS)


class PandasTaResult(BaseModel):
    indicator: str
    values: list[float | None]
    params: dict[str, int]
    message: str | None = None
    registry_version: str = REGISTRY_VERSION


def _candle_arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray[Any, Any]]:
    return {
        "open": np.array([float(c.open) for c in candles], dtype=np.float64),
        "high": np.array([float(c.high) for c in candles], dtype=np.float64),
        "low": np.array([float(c.low) for c in candles], dtype=np.float64),
        "close": np.array([float(c.close) for c in candles], dtype=np.float64),
        "volume": np.array([float(c.volume) for c in candles], dtype=np.float64),
    }


class PandasTaBridgeService:
    """캔들 -> `REGISTERED_INDICATORS` 지표값. 파라미터 검증·lookback은
    `PANDAS_TA_REGISTRY`(L02와 동일 계약)에 위임하고, 여기서는 pandas-ta-classic
    호출과 결과 포장만 한다 (`talib_adapter.IndicatorService`와 같은 형태)."""

    def __init__(self, registry: IndicatorRegistry = PANDAS_TA_REGISTRY) -> None:
        self._registry = registry

    def calculate(
        self, indicator: str, candles: Sequence[Candle], **params: int
    ) -> PandasTaResult:
        if indicator not in CALCULATORS:
            raise IndicatorError("STRATEGY_INDICATOR_UNKNOWN")
        try:
            self._registry.get(indicator)
        except IndicatorError:
            # A known calculator whose name the installed TA-Lib now provides:
            # refuse explicitly rather than run a second computation path.
            raise IndicatorError("PANDAS_TA_SUPERSEDED_BY_TALIB") from None
        resolved_params = self._registry.validate_params(indicator, params)
        min_required = self._registry.lookback(indicator, resolved_params) + 1

        if len(candles) < min_required:
            return PandasTaResult(
                indicator=indicator,
                values=[],
                params=resolved_params,
                message=f"데이터 부족, 최소 {min_required}개 필요",
            )

        columns = _candle_arrays(candles)
        series = CALCULATORS[indicator](columns, resolved_params)
        values = [None if pd.isna(v) else float(v) for v in series.to_numpy()]
        return PandasTaResult(indicator=indicator, values=values, params=resolved_params)
