"""U-1a `domain/query_plan.py` 단위 테스트(순수, DB 없음).

Spec: task-2628(U-1a) decision, L4_product_experience_and_discovery_v1.0.md
§2.2/§3("4종 필터 계획 생성, 리서치 as_of 강제", "조건 미래참조 거부").
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.foundation.screener.contracts.v1 import (
    BacktestStatFilter,
    Filter,
    FundamentalFilter,
    IndicatorFilter,
    ResearchFilter,
    ScreenDefinition,
    SortSpec,
)
from src.foundation.screener.domain.query_plan import ScreenerConditionError, build_query_plan

_DEFAULT_FILTERS: tuple[Filter, ...] = (IndicatorFilter(condition="ta.rsi(close, 14) < 30"),)


def _definition(
    *,
    universe: str = "kr_stocks",
    filters: tuple[Filter, ...] = _DEFAULT_FILTERS,
    sort: SortSpec | None = None,
    columns: tuple[str, ...] = ("close",),
) -> ScreenDefinition:
    return ScreenDefinition(universe=universe, filters=filters, sort=sort, columns=columns)


# ---- happy path: 4종 필터 전부 계획 생성 ----


def test_build_query_plan_compiles_all_four_filter_kinds() -> None:
    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per < 15 and roe > 0.1"),
            ResearchFilter(
                condition="eps_surprise > 0",
                as_of=datetime(2026, 9, 1, tzinfo=timezone.utc),
            ),
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        ),
        sort=SortSpec(field="per", direction="asc"),
    )

    plan = build_query_plan(definition)

    assert plan.universe == "kr_stocks"
    assert [f.kind for f in plan.filters] == [
        "indicator",
        "fundamental",
        "research",
        "backtest_stat",
    ]
    assert plan.sort_field == "per"
    assert plan.sort_direction == "asc"
    assert plan.columns == ("close",)


# ---- negative: 리서치 필터는 as_of 없이 인스턴스 자체를 만들 수 없다 ----


def test_research_filter_requires_as_of() -> None:
    with pytest.raises(ValidationError):
        ResearchFilter.model_validate({"condition": "eps_surprise > 0"})


# ---- negative: 조건 구문 오류 ----


def test_build_query_plan_rejects_syntax_error() -> None:
    definition = _definition(filters=(IndicatorFilter(condition="close >"),))

    with pytest.raises(ScreenerConditionError) as exc_info:
        build_query_plan(definition)

    assert exc_info.value.code == "SCREENER_CONDITION_SYNTAX"


# ---- negative: 조건이 불리언식이 아님(산술식 그대로 signal에 배정 불가) ----


def test_build_query_plan_rejects_non_boolean_condition() -> None:
    definition = _definition(filters=(IndicatorFilter(condition="close + 1"),))

    with pytest.raises(ScreenerConditionError) as exc_info:
        build_query_plan(definition)

    assert exc_info.value.code == "SCREENER_CONDITION_TYPE"


# ---- negative: 음수 postfix 인덱스(미래참조)는 파서 단계에서 이미 거부 ----


def test_build_query_plan_rejects_negative_postfix_index() -> None:
    definition = _definition(filters=(IndicatorFilter(condition="close[-1] > 0"),))

    with pytest.raises(ScreenerConditionError) as exc_info:
        build_query_plan(definition)

    assert exc_info.value.code == "SCREENER_CONDITION_SYNTAX"


# ---- negative: security() 류 미래 데이터 함수 호출은 lookahead가 거부 ----


def test_build_query_plan_rejects_future_data_function_call() -> None:
    definition = _definition(filters=(IndicatorFilter(condition="ta.security(close) > 0"),))

    with pytest.raises(ScreenerConditionError) as exc_info:
        build_query_plan(definition)

    assert exc_info.value.code == "SCREENER_CONDITION_LOOKAHEAD"


# ---- failure injection: 여러 필터 중 하나만 깨져도 계획 전체가 실패한다
# (부분 성공 없음 — fail-closed) ----


def test_build_query_plan_fails_closed_when_any_filter_is_invalid() -> None:
    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per <"),  # 주입된 결함
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        )
    )

    with pytest.raises(ScreenerConditionError):
        build_query_plan(definition)


# ---- negative: 필터가 비어 있으면 정의 자체가 성립하지 않는다 ----


def test_screen_definition_requires_at_least_one_filter() -> None:
    with pytest.raises(ValidationError):
        ScreenDefinition.model_validate({"universe": "kr_stocks", "filters": ()})


# ---- 성능 단언: ADR-2026-09-09-C 축별 예산("DSL 컴파일 300ms") 대비 여유 ----


@pytest.mark.perf
def test_build_query_plan_p95_latency_within_dsl_compile_budget() -> None:
    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per < 15 and roe > 0.1"),
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        )
    )
    samples: list[float] = []
    for _ in range(50):
        start = time.perf_counter()
        build_query_plan(definition)
        samples.append(time.perf_counter() - start)

    p95 = statistics.quantiles(samples, n=20)[18]
    assert p95 < 0.3  # ADR-2026-09-09-C 축별 예산: DSL 컴파일 300ms
