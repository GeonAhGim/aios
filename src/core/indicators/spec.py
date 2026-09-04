"""L01 — 지표 스펙 타입.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01

순수 데이터/타입 모듈 — I/O·계산 로직 없음. `IND-1`(지표 엔진)·L07(lookback)·
L28(series_cache)이 이 계약에 1:1 의존하므로 여기 정의한 필드 밖으로
임의 확장하지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

REGISTRY_VERSION = "ind-v1"


@dataclass(frozen=True)
class ParamSpec:
    """지표 파라미터 하나의 이름과 허용 범위."""

    name: str
    min: int
    max: int
    default: int


@dataclass(frozen=True)
class IndicatorSpec:
    """지표 하나의 계산 계약: 입력 시리즈, 파라미터, 출력, lookback."""

    name: str
    inputs: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    outputs: tuple[str, ...]
    lookback: Callable[[dict[str, int]], int]
    causal: bool = True
