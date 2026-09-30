"""tests/foundation/screener/__init__.py — screener 패키지 통합/부정/실패주입 테스트

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 — negative>=3, failure-injection>=1, perf assertion>=1

`contracts/v1.py`(ScreenDefinition) -> `domain/query_plan.py`(build_query_plan) ->
`domain/evaluate.py`(validate_plan_supported/matches_filters) 전체 파이프라인을
가로지르는 테스트. 개별 모듈의 단위 테스트는 test_query_plan.py/test_evaluate.py에
이미 있으므로, 여기서는 모듈 경계를 넘나드는 계약과 실패주입에 집중한다.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.screener.contracts.v1 import (
    MAX_CONDITION_SOURCE_LENGTH,
    IndicatorFilter,
    ScreenDefinition,
    SortSpec,
)
from src.foundation.screener.domain.evaluate import (
    RowFieldMissingError,
    ScreenerEvaluationError,
    matches_filters,
    required_field_names,
    validate_plan_supported,
)
from src.foundation.screener.domain.query_plan import (
    ScreenerConditionError,
    build_query_plan,
)


def _definition(
    *,
    condition: str = "close > 100",
    sort_field: str | None = None,
    columns: tuple[str, ...] = (),
) -> ScreenDefinition:
    return ScreenDefinition(
        universe="KIS_KRX",
        filters=(IndicatorFilter(condition=condition),),
        sort=SortSpec(field=sort_field, direction="asc") if sort_field else None,
        columns=columns,
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests — 모듈 경계를 넘는 불변식 위반 입력 거부
# ──────────────────────────────────────────────────────────────────────


def test_negative_condition_source_over_max_length_rejected_at_contract() -> None:
    """부정: condition 길이가 MAX_CONDITION_SOURCE_LENGTH를 넘으면 계약 단계에서 거부되고
    domain/query_plan.py까지 도달하지 않는다 (fail fast at the boundary)."""
    too_long = "close > 0 and " * (MAX_CONDITION_SOURCE_LENGTH // len("close > 0 and ") + 1)
    with pytest.raises(ValidationError):
        IndicatorFilter(condition=too_long)


def test_negative_whitespace_only_condition_rejected_at_contract() -> None:
    """부정: 공백만 있는 condition은 strip 후 빈 문자열이 되어 계약 단계에서 거부된다."""
    with pytest.raises(ValidationError):
        IndicatorFilter(condition="   \n\t  ")


def test_negative_sort_direction_outside_asc_desc_rejected() -> None:
    """부정: SortSpec.direction은 Literal["asc","desc"] 밖의 값을 받지 않는다."""
    with pytest.raises(ValidationError):
        SortSpec(field="close", direction="ascending")  # type: ignore[arg-type]


def test_negative_sort_field_outside_registry_rejected_end_to_end() -> None:
    """부정: 필터 조건 자체는 유효해도, sort_field가 인식되지 않는 필드명이면
    validate_plan_supported가 파이프라인 끝에서 여전히 거부한다 (sort_field도
    required_field_names에 포함되므로 필터만 검사하는 것으로는 충분하지 않음)."""
    definition = _definition(condition="close > 100", sort_field="market_cap")
    plan = build_query_plan(definition)
    assert "market_cap" in required_field_names(plan)
    with pytest.raises(ScreenerEvaluationError) as exc_info:
        validate_plan_supported(plan)
    assert exc_info.value.code == "SCREENER_EVAL_UNKNOWN_FIELD"


def test_negative_unrecognized_column_rejected_end_to_end() -> None:
    """부정: columns에 인식되지 않는 필드명을 넣으면 필터/정렬이 정상이어도 거부된다."""
    definition = _definition(condition="close > 100", columns=("close", "dividend_yield"))
    plan = build_query_plan(definition)
    with pytest.raises(ScreenerEvaluationError) as exc_info:
        validate_plan_supported(plan)
    assert exc_info.value.code == "SCREENER_EVAL_UNKNOWN_FIELD"


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


def test_failure_injection_unexpected_lookahead_dependency_error_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: build_query_plan은 ScreenerConditionError만 잡아 재포장한다 — 의존성
    (check_lookahead)이 예상 밖 예외를 던지면 그 예외가 그대로 전파되어야 하고,
    조용히 삼켜져 "컴파일 성공"으로 위장해서는 안 된다 (fail-closed)."""
    import src.foundation.screener.domain.query_plan as query_plan_module

    def _boom(tokens: object) -> None:
        raise RuntimeError("injected lookahead dependency failure")

    monkeypatch.setattr(query_plan_module, "check_lookahead", _boom)

    definition = _definition(condition="close > 100")
    with pytest.raises(RuntimeError, match="injected lookahead dependency failure"):
        build_query_plan(definition)


def test_failure_injection_row_missing_field_excludes_row_not_whole_scan() -> None:
    """실패주입: 일부 심볼의 row에서 필드가 누락돼도(예: 신규 상장 직후 캔들 없음) 그
    한 행만 RowFieldMissingError로 배제되고, 다른 정상 row 평가에는 영향이 없다 —
    application/run_screen.py가 기대하는 부분 배제 계약."""
    plan = build_query_plan(_definition(condition="close > 100"))
    good_row = {"close": Decimal("150")}
    bad_row: dict[str, Decimal] = {}

    assert matches_filters(plan, good_row) is True
    with pytest.raises(RowFieldMissingError) as exc_info:
        matches_filters(plan, bad_row)
    assert exc_info.value.field_name == "close"
    # 배제 이후에도 동일 plan 객체로 다른 행을 계속 평가할 수 있다 (plan이 오염되지 않음)
    assert matches_filters(plan, good_row) is True


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_perf_full_pipeline_compile_plus_validate_plus_evaluate() -> None:
    """성능 단언: 계약 검증 -> 컴파일 -> 지원 여부 검증 -> 100행 평가 전체 파이프라인의
    p95가 ADR-2026-09-09-C 축별 예산(DSL 컴파일 300ms) 안에 들어와야 한다."""
    rows = [{"close": Decimal(100 + i), "volume": Decimal(1000)} for i in range(100)]
    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        definition = _definition(condition="close > 100 and volume < 2000")
        plan = build_query_plan(definition)
        validate_plan_supported(plan)
        for row in rows:
            matches_filters(plan, row)
        samples.append(time.perf_counter() - start)

    p95 = statistics.quantiles(samples, n=20)[18]
    assert p95 < 0.3  # ADR-2026-09-09-C 축별 예산: DSL 컴파일 300ms


# ──────────────────────────────────────────────────────────────────────
# 4. Cross-module integration tests
# ──────────────────────────────────────────────────────────────────────


def test_integration_research_filter_as_of_survives_full_pipeline() -> None:
    """통합: ResearchFilter(as_of 필수, RD-A1)가 build_query_plan을 거쳐도 compiled
    필터 목록에서 research kind로 남아있는다 (계약 불변식이 domain 계층을 통과해도
    깨지지 않음)."""
    from src.foundation.screener.contracts.v1 import ResearchFilter

    definition = ScreenDefinition(
        universe="KIS_KRX",
        filters=(
            IndicatorFilter(condition="close > 100"),
            ResearchFilter(
                condition="eps_surprise > 0",
                as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ),
    )
    plan = build_query_plan(definition)
    assert [f.kind for f in plan.filters] == ["indicator", "research"]
    # research 필터의 필드(eps_surprise)는 OHLCV 레지스트리 밖이므로 평가 엔진은
    # 여전히 거부한다 — 계약 통과와 실행 가능은 서로 다른 계약이다 (IND-12 follow-up)
    with pytest.raises(ScreenerEvaluationError) as exc_info:
        validate_plan_supported(plan)
    assert exc_info.value.code == "SCREENER_EVAL_UNKNOWN_FIELD"


def test_integration_valid_plan_compiles_validates_and_evaluates() -> None:
    """통합: OHLCV 필드만 쓰는 정상 플랜은 컴파일 -> 지원 검증 -> 평가 3단계를 모두
    통과하고, 각 단계가 독립적으로 부정 케이스도 정확히 구분해서 거부한다."""
    definition = _definition(condition="close > 100 and volume < 1000", sort_field="close")
    plan = build_query_plan(definition)
    validate_plan_supported(plan)  # must not raise

    assert matches_filters(plan, {"close": Decimal("150"), "volume": Decimal("500")}) is True
    assert matches_filters(plan, {"close": Decimal("50"), "volume": Decimal("500")}) is False


def test_integration_syntax_error_short_circuits_before_evaluation_engine() -> None:
    """통합: 구문 오류가 있는 조건은 query_plan 단계에서 이미 걸러지므로,
    evaluate.py의 validate_plan_supported/matches_filters까지 절대 도달하지 않는다
    (호출 순서 계약: contracts -> query_plan -> evaluate)."""
    with pytest.raises(ScreenerConditionError) as exc_info:
        build_query_plan(_definition(condition="close >"))
    assert exc_info.value.code == "SCREENER_CONDITION_SYNTAX"
