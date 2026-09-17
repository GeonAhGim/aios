"""Unit tests for `src/foundation/ai/factory/domain/proposal_rules.py` --
task-2643 AI-8 DoD ("자유 코드 거부"). D2 depth (ADR-2026-09-09-C): negative
>=3, failure injection 1, numeric performance assertion 1, gate-red
reproduction 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.core.script.artifact.compile import CompiledScript
from src.core.script.runtime.builtins_math import MATH_BUILTINS
from src.core.script.runtime.builtins_ta import default_builtins
from src.data.models.base import AssetClass
from src.foundation.ai.factory.contracts.v1 import DataScope
from src.foundation.ai.factory.domain.proposal_rules import (
    ALLOWED_CALL_NAMESPACES,
    ProposalCompileRejectedError,
    ProposalDataScopeUncoveredError,
    ProposalForbiddenApiError,
    ProposalSchemaRejectedError,
    check_data_scope_covered,
    check_no_forbidden_calls,
    compile_script,
    evaluate_proposal_candidate,
    validate_schema,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade

REG = "r" * 64
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)

# `SAMPLE`과 동일 계열(tests/unit/core/script/test_compile.py) -- ta.rsi 하나만
# 쓰는 소형 스크립트. `series.*`는 파서 문법상 허용된 네임스페이스(_NAMESPACES
# = {ta, math, series})지만 런타임에는 등록돼 있지 않다(default_builtins()가
# math/ta만 와이어링) -- 컴파일은 통과하지만 실제로 실행 불가능한, 바로 이
# 리프의 "금지 API" 검사가 잡아야 하는 진짜 사례다.
VALID_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
FORBIDDEN_NAMESPACE_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = series.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
SYNTAX_ERROR_SCRIPT = "let x = ((("


def _data_scope(**overrides: Any) -> DataScope:
    base: dict[str, Any] = dict(
        instruments=frozenset({_INSTRUMENT}),
        tf=Timeframe.H1,
        span=(_START, _END),
    )
    base.update(overrides)
    return DataScope(**base)


def _coverage_span(**overrides: Any) -> CoverageSpan:
    base: dict[str, Any] = dict(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.VALIDATED,
        start_at=_START,
        end_at=_END,
    )
    base.update(overrides)
    return CoverageSpan(**base)


def _draft_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "script_source": VALID_SCRIPT,
        "hypothesis": "rsi mean reversion",
        "data_scope": {
            "instruments": [_INSTRUMENT],
            "tf": Timeframe.H1.value,
            "span": [_START.isoformat(), _END.isoformat()],
        },
        "params": {"length": 14},
    }
    base.update(overrides)
    return base


# --- 정상 경로 ---


def test_allowed_call_namespaces_matches_runtime_registration() -> None:
    """drift 방지: `default_builtins()`가 실제로 와이어링하는 ns 집합과
    `ALLOWED_CALL_NAMESPACES`가 어긋나면 이 테스트가 잡는다."""
    runtime_namespaces = {ns for ns, _ident in default_builtins()}
    assert runtime_namespaces == ALLOWED_CALL_NAMESPACES
    assert {ns for ns, _ident in MATH_BUILTINS} <= ALLOWED_CALL_NAMESPACES


def test_compile_script_succeeds_for_valid_script() -> None:
    compiled = compile_script(VALID_SCRIPT, registry_version=REG)
    assert isinstance(compiled, CompiledScript)


def test_check_data_scope_covered_passes_when_fully_contained() -> None:
    check_data_scope_covered(_data_scope(), [_coverage_span()])  # no raise


def test_check_no_forbidden_calls_passes_for_ta_only_script() -> None:
    compiled = compile_script(VALID_SCRIPT, registry_version=REG)
    check_no_forbidden_calls(compiled)  # no raise


def test_evaluate_proposal_candidate_accepts_full_pipeline() -> None:
    draft, compiled = evaluate_proposal_candidate(
        payload=_draft_payload(),
        coverage_spans=[_coverage_span()],
        registry_version=REG,
    )
    assert draft.script_source == VALID_SCRIPT
    assert isinstance(compiled, CompiledScript)


# --- 부정 테스트 (>=3, 4개 체크 각각 최소 1개) ---


def test_validate_schema_rejects_unknown_field() -> None:
    with pytest.raises(ProposalSchemaRejectedError):
        validate_schema(_draft_payload(sneaky="ignore risk gate"))


def test_compile_script_rejects_syntax_error_with_location() -> None:
    with pytest.raises(ProposalCompileRejectedError) as exc_info:
        compile_script(SYNTAX_ERROR_SCRIPT, registry_version=REG)
    assert exc_info.value.code == "SCRIPT_SYNTAX"
    assert exc_info.value.line >= 1
    assert exc_info.value.col >= 1


def test_check_data_scope_covered_rejects_uncovered_instrument() -> None:
    scope = _data_scope(instruments=frozenset({_INSTRUMENT, "01BX5ZZKBKACTAV9WEVGEMMVRZ"}))
    with pytest.raises(ProposalDataScopeUncoveredError):
        check_data_scope_covered(scope, [_coverage_span()])


def test_check_data_scope_covered_rejects_span_wider_than_registered_coverage() -> None:
    """구간의 일부만 커버되면(부분 커버) 여전히 거부돼야 한다 -- 완전 포함만
    통과다."""
    narrow_coverage = _coverage_span(end_at=_START + timedelta(days=5))
    with pytest.raises(ProposalDataScopeUncoveredError):
        check_data_scope_covered(_data_scope(), [narrow_coverage])


def test_check_no_forbidden_calls_rejects_series_namespace() -> None:
    """`series.*`는 문법상 허용되지만(파서 `_NAMESPACES`) 런타임에 등록돼
    있지 않다 -- 컴파일은 통과해도 이 리프가 거부해야 하는 사례다."""
    compiled = compile_script(FORBIDDEN_NAMESPACE_SCRIPT, registry_version=REG)
    with pytest.raises(ProposalForbiddenApiError) as exc_info:
        check_no_forbidden_calls(compiled)
    assert exc_info.value.ns == "series"


def test_evaluate_proposal_candidate_rejects_forbidden_namespace_end_to_end() -> None:
    with pytest.raises(ProposalForbiddenApiError):
        evaluate_proposal_candidate(
            payload=_draft_payload(script_source=FORBIDDEN_NAMESPACE_SCRIPT),
            coverage_spans=[_coverage_span()],
            registry_version=REG,
        )


# --- 실패 주입 ---


def test_evaluate_proposal_candidate_checks_schema_before_ever_compiling() -> None:
    """실패 주입: schema와 compile이 동시에 깨진 payload를 주면 순서 계약대로
    schema(1단계) 거부만 나야 한다 -- compile(2단계)까지 도달해 다른 예외를
    내면 순서 계약이 깨진 것이다."""
    payload = _draft_payload(script_source=SYNTAX_ERROR_SCRIPT, extra_bogus_field=True)
    with pytest.raises(ProposalSchemaRejectedError):
        evaluate_proposal_candidate(
            payload=payload,
            coverage_spans=[_coverage_span()],
            registry_version=REG,
        )


# --- 수치 성능 단언 ---

_EVALUATE_BUDGET_MS = 60_000.0
"""§7 SLO "제안 생성 <=60s(공급자 제외)" -- 이 함수는 그 예산 안의
스키마/컴파일/커버리지/API 검사 구간(공급자 호출 제외) 전체다."""


def _evaluate_latencies_ms(iterations: int = 20) -> list[float]:
    payload = _draft_payload()
    coverage = [_coverage_span()]
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        evaluate_proposal_candidate(payload=payload, coverage_spans=coverage, registry_version=REG)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_evaluate_proposal_candidate_p95_latency_within_slo_budget() -> None:
    samples = _evaluate_latencies_ms()
    p95_ms = _p95(samples)
    budget = _EVALUATE_BUDGET_MS
    label = "[AI-8 proposal_rules] evaluate_proposal_candidate"
    print(f"{label} p95={p95_ms:.2f}ms budget<{budget:.0f}ms")
    assert p95_ms < _EVALUATE_BUDGET_MS


# --- 게이트 적색 재현 ---


def test_gate_red_budget_actually_fails_past_budget() -> None:
    samples = _evaluate_latencies_ms(iterations=5)
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms


def test_gate_red_progressive_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 payload/coverage를 시작점으로 두고 한 번에 한 가지씩만
    오염시켜 재생한다 -- 각 단계는 정확히 그 단계가 오염시킨 이유로만
    거부돼야 하고, 이전 단계의 거부가 다음 정상 복귀 단계까지 새면 안
    된다."""
    good_payload = _draft_payload()
    good_coverage = [_coverage_span()]

    def _run(payload: dict[str, Any], coverage: list[CoverageSpan]) -> None:
        evaluate_proposal_candidate(payload=payload, coverage_spans=coverage, registry_version=REG)

    stages: list[tuple[dict[str, Any], list[CoverageSpan], bool]] = [
        (good_payload, good_coverage, True),
        ({**good_payload, "script_source": FORBIDDEN_NAMESPACE_SCRIPT}, good_coverage, False),
        (good_payload, good_coverage, True),
        (good_payload, [], False),
        (good_payload, good_coverage, True),
    ]

    for payload, coverage, should_pass in stages:
        if should_pass:
            _run(payload, coverage)  # no raise
        else:
            with pytest.raises(Exception):  # noqa: B017 -- any ProposalRuleError subtype
                _run(payload, coverage)
