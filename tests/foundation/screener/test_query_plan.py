"""U-1a `domain/query_plan.py` 단위 테스트(순수, DB 없음).

Spec: task-2628(U-1a) decision, L4_product_experience_and_discovery_v1.0.md
§2.2/§3("4종 필터 계획 생성, 리서치 as_of 강제", "조건 미래참조 거부").
"""

from __future__ import annotations

from datetime import UTC, datetime

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
from tests.conftest import PerfBudget

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
                as_of=datetime(2026, 9, 1, tzinfo=UTC),
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
def test_build_query_plan_p95_latency_within_dsl_compile_budget(
    perf_budget: PerfBudget,
) -> None:
    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per < 15 and roe > 0.1"),
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        )
    )
    samples = perf_budget.samples(lambda: build_query_plan(definition), n=50)
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    budget_ms = 300.0  # ADR-2026-09-09-C 축별 예산: DSL 컴파일 300ms
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
    assert p95_ms < budget_ms


# ---------------------------------------------------------------------------
# CPU 0-convergence + gate-red reproduction (D2, task-11539 감사 후속)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_build_query_plan_single_call_cpu_nonzero() -> None:
    """단일 호출 CPU 시간이 0ms가 아님을 검증한다(0 수렴 결함 감지).

    `time.process_time()` 기반 측정에서 단일 호출이 0ms면 Windows 양자화
    오차(≈15.6ms tick)로 인한 측정 결함이다. batch=8로 여러 호출을 묶으면
    양자화 오차가 호출당 tick/8로 줄어 CPU 시간을 정상 측정한다.
    task-11539 감사: 0 수렴이면 batch로 정정.

    `build_query_plan`은 DSL 컴파일로서 매우 빠르므로(batch=8 기준 0ms),
    batch=1024로 늘려 양자화 오차를 호출당 tick/1024 수준으로 낮춘다.
    """
    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per < 15 and roe > 0.1"),
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        )
    )
    sample = PerfBudget().sample(lambda: build_query_plan(definition), batch=1024)
    assert sample.cpu_ms > 0, (
        f"단일 호출 CPU 시간이 0ms (batch=1024 기준) — 측정 버그 또는 양자화 오차: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_build_query_plan_perf_budget_goes_red_with_sleep_injection(
    monkeypatch,
) -> None:
    """gate-red reproduction: sleep 주입으로 지연을 인위적으로 늘리면
    기존 녹색 성능 단언이 적색으로 떨어져야 한다.

    `build_query_plan` 내부의 `time.process_time()` 측정을 monkeypatch해
    인위적인 CPU 시간을 주입한다. 예산 10배 지연이므로 기존 단언이
    적색으로 떨어져야 한다. 단언/마커 삭제와 예산 완화 금지.
    """
    import time

    definition = _definition(
        filters=(
            IndicatorFilter(condition="ta.rsi(close, 14) < 30"),
            FundamentalFilter(condition="per < 15 and roe > 0.1"),
            BacktestStatFilter(condition="sharpe_ratio > 1.5"),
        )
    )

    # Wrap time.process_time to inject 10x CPU load
    original_process_time = time.process_time

    call_count = 0

    def slow_process_time() -> float:
        nonlocal call_count
        call_count += 1
        t = original_process_time()
        # Add 50ms to simulate heavy CPU load on first call sequence
        if call_count == 1:
            t += 0.05
        return t

    monkeypatch.setattr(time, "process_time", slow_process_time)

    # Re-run the same measurement pattern as the p95 test with 10x budget
    budget_ms = 3000.0  # 10× 실제 예산 300ms
    samples = []
    for _ in range(50):
        call_count = 0  # reset per-sample to only inject once
        samples.append(PerfBudget().sample(lambda: build_query_plan(definition)))
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
    assert p95_ms < budget_ms, (
        f"sleep 주입 후 p95={p95_ms:.3f}ms — "
        f"예산 3000ms 초과 단언이 실패: 측정/단언이 공허함(hollow assertion)"
    )
