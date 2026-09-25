"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-12 —
`artifact/compile.py`(DSL-2→7 파이프라인 조립) 테스트.

DoD "4종 오류 코드"·"오류 위치 응답": `SCRIPT_SYNTAX`·`SCRIPT_TYPE`·
`SCRIPT_LOOKAHEAD`·`SCRIPT_RESOURCE_LIMIT` 각각이 `ScriptCompileError`로
(line, col)과 함께 나오는지, 타입·자원 오류의 위치가 "원인 선언"의 시작
키워드 위치로 복원되는지(접두 이분탐색), DSL-7 `ScriptLowerError`는 감싸지지
않고 전파되는지(taxonomy 밖 = 계약 위반) 단언한다.

DEEPEN(task-2918): 성능(ADR-2026-09-09-C Decision 1 "DSL 컴파일 300ms" 예산)은
원래 print 실측만 하고 단언을 의도적으로 피했다(task-1535 note "절대 지연 단언
금지", task-2727 DEPTH 감사가 ADR 예산 위반 소지로 지적) — 이 리프에서 실제
수치 단언으로 승격하고, 파이프라인 한 단계가 예산을 실제로 넘기면 그 단언이
정말로 실패(=CI 적색)하는지도 재현한다.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

import pytest

from src.core.script.analysis.resources import ResourceLimits, check_resources
from src.core.script.artifact import (
    SCRIPT_ERROR_CODES,
    CompiledScript,
    ScriptCompileError,
    compile_source,
    decl_positions,
    script_hash,
)
from src.core.script.grammar.ast import GRAMMAR_VERSION, Program
from src.core.script.grammar.lexer import tokenize
from src.core.script.grammar.parser import parse
from src.core.script.ir import IR_VERSION, IRProgram, ScriptLowerError, to_bytes
from src.core.script.ir.lower import lower_program
from src.core.script.typing.types import Type

SAMPLE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "let prev = close[1]\n"
    "signal go_long = rsi_val < 30 and close > prev\n"
    "plot(rsi_val, 1)\n"
    "order(buy, 1, 2) when go_long"
)
REG = "r" * 64


def _err(source: str, **kw: object) -> ScriptCompileError:
    with pytest.raises(ScriptCompileError) as info:
        compile_source(source, registry_version=REG, **kw)  # type: ignore[arg-type]
    return info.value


# ---- 성공 경로 ----


def test_compile_success_returns_hash_ir_and_estimate() -> None:
    compiled = compile_source(SAMPLE, registry_version=REG)
    assert isinstance(compiled, CompiledScript)
    assert compiled.ir_bytes == to_bytes(compiled.ir)
    assert compiled.estimate == check_resources(parse(SAMPLE))
    assert compiled.script_hash == script_hash(source=SAMPLE, ir=compiled.ir, registry_version=REG)
    assert compiled.grammar_version == GRAMMAR_VERSION
    assert compiled.ir_version == IR_VERSION
    assert compiled.registry_version == REG


def test_compile_is_deterministic() -> None:
    a = compile_source(SAMPLE, registry_version=REG)
    b = compile_source(SAMPLE, registry_version=REG)
    assert a.script_hash == b.script_hash
    assert a.ir_bytes == b.ir_bytes


def _compile_latencies_sec(source: str, registry_version: str, iterations: int = 30) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        compile_source(source, registry_version=registry_version)
        samples.append(time.perf_counter() - started)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_compile_p95_latency_within_adr_budget() -> None:
    """DEEPEN(task-2918): ADR-2026-09-09-C Decision 1 'DSL 컴파일 300ms' 예산을
    실제로 단언한다(이전 `test_compile_elapsed_print_only`는 print만 하고 단언을
    의도적으로 피했다 — task-2727 DEPTH 감사가 ADR 예산 위반 소지로 지적)."""
    samples = _compile_latencies_sec(SAMPLE, REG)
    p95_ms = _p95(samples) * 1000
    budget_ms = 300.0
    print(f"[DSL-12] compile_source p95={p95_ms:.2f}ms budget<{budget_ms:.0f}ms (n={len(samples)})")
    assert p95_ms < budget_ms


def test_compile_still_returns_correct_result_when_a_pipeline_stage_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: DSL-7 로우어링 단계가 실제로 느려지면(예: 외부 지표 레지스트리
    조회 hang) `compile_source`가 그 지연을 숨기지 않고 그대로 감내한 뒤에도
    여전히 올바른 결과를 내는지 확인한다 — 위 p95 단언이 실제 파이프라인 전체의
    벽시계를 재는 것이지, 내부적으로 캐시되거나 지름길을 타는 값을 재는 게
    아님을 보장한다."""
    original_lower = lower_program
    delay_s = 0.05

    def _stalled_lower(program: Program, env: Mapping[str, Type] | None = None) -> IRProgram:
        time.sleep(delay_s)
        return original_lower(program, env)

    monkeypatch.setattr("src.core.script.artifact.compile.lower_program", _stalled_lower)

    started = time.perf_counter()
    compiled = compile_source(SAMPLE, registry_version=REG)
    elapsed = time.perf_counter() - started

    # Windows 타이머 해상도상 time.sleep(delay_s)가 요청한 시간보다 근소하게(<1ms) 일찍
    # 반환할 수 있어, 지연이 실제로 감내됐는지는 90% 문턱으로 확인한다(정확히 delay_s 이상을
    # 요구하면 타이머 해상도 노이즈로 flaky해진다).
    assert elapsed >= delay_s * 0.9
    assert compiled.script_hash == script_hash(source=SAMPLE, ir=compiled.ir, registry_version=REG)


def test_compile_budget_gate_actually_fails_when_pipeline_stalls_past_300ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 파이프라인 한 단계가 300ms 예산을 실제로 넘기도록 지연을
    주입하면, `test_compile_p95_latency_within_adr_budget`과 동일한 단언식이
    실제로 `AssertionError`를 내는지(= CI가 실제로 빨간불이 되는지) 확인한다.
    이 테스트가 없으면 위 단언이 항상 통과하는 무의미한 단언(tautology)인지
    아무도 검증하지 못한다."""
    original_lower = lower_program

    def _stalled_lower(program: Program, env: Mapping[str, Type] | None = None) -> IRProgram:
        time.sleep(0.35)  # > 300ms 예산
        return original_lower(program, env)

    monkeypatch.setattr("src.core.script.artifact.compile.lower_program", _stalled_lower)

    samples = _compile_latencies_sec(SAMPLE, REG, iterations=3)
    p95_ms = _p95(samples) * 1000
    budget_ms = 300.0
    with pytest.raises(AssertionError):
        assert p95_ms < budget_ms


# ---- 4종 오류 + 위치 ----


def test_syntax_error_from_lexer_has_token_position() -> None:
    err = _err("input x: int = 1\nlet y = 2 @ 3")
    assert err.code == "SCRIPT_SYNTAX"
    assert (err.line, err.col) == (2, 11)


def test_syntax_error_from_parser_has_token_position() -> None:
    err = _err("let a = 1 +")
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 12


def test_type_error_position_is_the_failing_decl_start() -> None:
    source = (
        "input a: int = 1\n"
        "input c: series<float> = 0\n"
        "let ok = c + a\n"
        "let bad = c + missing\n"  # 4번째 선언이 원인
        "signal s = ok > 1\n"
    )
    err = _err(source)
    assert err.code == "SCRIPT_TYPE"
    assert (err.line, err.col) == (4, 1)


def test_type_error_position_respects_indentation_column() -> None:
    err = _err("input a: int = 1\n    let bad = zz")
    assert err.code == "SCRIPT_TYPE"
    assert (err.line, err.col) == (2, 5)


def test_type_error_on_first_decl_points_to_line_one() -> None:
    err = _err("let bad = zz\ninput a: int = 1")
    assert err.code == "SCRIPT_TYPE"
    assert (err.line, err.col) == (1, 1)


def test_lookahead_error_has_future_call_token_position() -> None:
    err = _err("input c: series<float> = 0\nlet a = ta.security(c, 1)")
    assert err.code == "SCRIPT_LOOKAHEAD"
    assert (err.line, err.col) == (2, 12)


def test_resource_limit_position_is_the_decl_that_crosses_the_limit() -> None:
    source = "input c: series<float> = 0\nplot(c)\nplot(c)\nplot(c)\n"
    err = _err(source, limits=ResourceLimits(max_plots=2))
    assert err.code == "SCRIPT_RESOURCE_LIMIT"
    assert (err.line, err.col) == (4, 1)  # 3번째 plot = 4번째 선언


def test_negative_index_is_syntax_not_lookahead_by_pipeline_order() -> None:
    """DSL-3 파서가 DSL-5보다 먼저 돌므로 음수 인덱스는 SCRIPT_SYNTAX다(모듈 docstring)."""
    err = _err("input c: series<float> = 0\nlet a = c[-1]")
    assert err.code == "SCRIPT_SYNTAX"
    assert (err.line, err.col) == (2, 11)


def test_all_four_codes_reachable() -> None:
    seen = {
        _err("let a = 1 +").code,
        _err("let a = zz").code,
        _err("input c: series<float> = 0\nlet a = ta.security(c, 1)").code,
        _err(
            "input c: series<float> = 0\nplot(c)\nplot(c)", limits=ResourceLimits(max_plots=1)
        ).code,
    }
    assert seen == SCRIPT_ERROR_CODES


def test_error_details_shape_for_api_envelope() -> None:
    err = _err("let a = zz")
    assert err.details == {"code": "SCRIPT_TYPE", "line": 1, "col": 1}
    assert "SCRIPT_TYPE" in str(err) and "line 1" in str(err)


# ---- negative: taxonomy 밖 ----


def test_unknown_code_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="taxonomy"):
        ScriptCompileError("SCRIPT_LOWER", "x", 1, 1)


def test_lower_error_is_not_wrapped() -> None:
    non_finite = "let a = " + "9" * 400 + ".0"
    with pytest.raises(ScriptLowerError):
        compile_source(non_finite, registry_version=REG)


def test_empty_registry_version_rejected_before_returning() -> None:
    with pytest.raises(ValueError, match="레지스트리"):
        compile_source(SAMPLE, registry_version="")


# ---- 위치 복원 보조 ----


def test_decl_positions_matches_decl_count() -> None:
    tokens = tokenize(SAMPLE)
    positions = decl_positions(tokens)
    assert len(positions) == len(parse(SAMPLE).decls) == 7
    assert positions[0] == (1, 1)
    assert positions[-1] == (7, 1)
