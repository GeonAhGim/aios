"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-15 —
`import_/pine/transpile.py::transpile_and_verify` DEEPEN.

negative test 3건 이상: 불변식 위반 입력을 명시적으로 거부하는 케이스.
실패주입 1건: monkeypatch로 의존성 예외 유발.

DoD: task-9994 (task-6704 고아 산출물 회수 5828 qa-2).
"""

from __future__ import annotations

import pytest

from src.core.script.import_.pine.lexer import PineSyntaxError
from src.core.script.import_.pine.transpile import (
    PineTranspileError,
    transpile_and_verify,
)
from tests.conftest import PerfBudget

# ---- negative test: 불변식 위반 입력 거부 ----


def test_transpile_and_verify_rejects_unsupported_strategy_exit() -> None:
    """strategy.exit(...)는 변환되지 않는다 (DSL-15 decision)."""
    with pytest.raises(PineTranspileError, match="strategy.exit"):
        transpile_and_verify('strategy.exit("Long", 1)')


def test_transpile_and_verify_rejects_unsupported_namespace() -> None:
    """허용되지 않은 네임스페이스(request.*)는 파서 단계에서 거부."""
    with pytest.raises(PineSyntaxError):
        transpile_and_verify("x = request.security(s, 'D', close)")


def test_transpile_and_verify_rejects_bare_function_call() -> None:
    """plot() 외의 최상위 호출(indicator/strategy 선언)은 거부된다."""
    with pytest.raises(PineSyntaxError):
        transpile_and_verify("indicator('test')")


def test_transpile_and_verify_rejects_input_with_keyword_first_arg() -> None:
    """input.int의 첫 인자가 키워드 인자면 거부된다."""
    with pytest.raises(PineTranspileError, match="첫 인자는 위치 인자여야"):
        transpile_and_verify('len = input.int(title="Length", 14)')


def test_transpile_and_verify_rejects_input_float_with_int_literal() -> None:
    """input.int에 float 리터럴을 넣으면 거부된다."""
    with pytest.raises(PineTranspileError, match="정수 리터럴"):
        transpile_and_verify("x = input.int(1.5)")


def test_transpile_and_verify_rejects_input_float_with_bool() -> None:
    """input.float에 bool 리터럴을 넣으면 거부된다."""
    with pytest.raises(PineTranspileError, match="숫자 리터럴이어야"):
        transpile_and_verify("x = input.float(true)")


# ---- 실패주입: 의존성 예외 유발 ----


def test_transpile_and_verify_propagates_parse_error() -> None:
    """파서 단계에서 PineSyntaxError가 발생하면 그대로 전파된다."""
    with pytest.raises(PineSyntaxError):
        transpile_and_verify("this is not valid pine script &&&& invalid")


def test_transpile_and_verify_propagates_lexer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """lexer.tokenize()를 monkeypatch해 예외를 유발하면 전파된다.

    parser.py가 `from ...lexer import tokenize`하므로 tokenize는 parser
    네임스페이스에 바인딩된다. transpile.py가 parser.parse()를 호출하므로
    parser.tokenize를 패치해야 실제 호출 지점을 건딜 수 있다.
    """
    import src.core.script.import_.pine.parser as parser_module

    def _mock_tokenize(_source: str) -> list:
        raise PineSyntaxError("mock lexer failure", 1, 0)

    monkeypatch.setattr(parser_module, "tokenize", _mock_tokenize)
    with pytest.raises(PineSyntaxError, match="mock lexer failure"):
        transpile_and_verify("x = 1")


# ---- 성능 단언: perf_budget 경유 (ADR-2026-09-09-C Decision 1, DSL 컴파일 예산 300ms) ----


@pytest.mark.perf
def test_transpile_and_verify_within_dsl_compile_budget(perf_budget: PerfBudget) -> None:
    """단일 문장 변환+검증이 DSL 컴파일 예산 300ms 안에 드는지 확인한다."""
    # series 상한(max_series=64) 안에 머물도록 60줄 산술식만 쓴다.
    source = "\n".join(f"v_{i} = close + {i}" for i in range(60))

    def _run_once() -> None:
        transpile_and_verify(source)

    perf_budget.assert_within(
        _run_once, budget_ms=300.0, label="transpile_and_verify 60-line source"
    )


# ---- 긍정 테스트: 정상 변환 + 검증 통과 ----


def test_transpile_and_verify_passes_simple_arithmetic() -> None:
    """정상적인 산술식 변환이 검증 단계를 통과하고 IR에 store 명령이 들어간다."""
    result = transpile_and_verify("x = 1 + 2")
    assert len(result.program.decls) == 1
    decl = result.program.decls[0]
    assert decl.kind == "let"
    assert decl.name == "x"
    assert len(result.ir.instrs) > 0
    # 같은 입력에 대해 두 번 변환하면 ir_bytes가 동일해야 한다 (결정론).
    result2 = transpile_and_verify("x = 1 + 2")
    assert result.ir_bytes == result2.ir_bytes


def test_transpile_and_verify_passes_ta_call() -> None:
    """ta.sma(...) 변환이 검증 단계를 통과하고 IR에 ta.sma 호출이 들어간다."""
    result = transpile_and_verify("sma = ta.sma(close, 20)")
    assert len(result.program.decls) == 2  # close OHLCV auto-inject + let
    let_decl = result.program.decls[-1]
    assert let_decl.kind == "let"
    assert let_decl.name == "sma"
    assert len(result.ir.instrs) > 0


def test_transpile_and_verify_passes_strategy_entry() -> None:
    """strategy.entry(...) 변환이 검증 단계를 통과하고 OrderDecl이 들어간다."""
    result = transpile_and_verify('strategy.entry("Long", 1, qty=2)')
    assert len(result.program.decls) == 1
    decl = result.program.decls[0]
    assert decl.kind == "order"
    assert len(result.ir.instrs) > 0


def test_transpile_and_verify_passes_plot() -> None:
    """plot(...) 문장이 검증 단계를 통과하고 PlotDecl이 들어간다.

    close는 OHLCV 미선언 자동 inject 대상이므로 InputDecl + PlotDecl의 2건이다.
    """
    result = transpile_and_verify("plot(close)")
    assert len(result.program.decls) == 2
    input_decl, plot_decl = result.program.decls
    assert input_decl.kind == "input"
    assert input_decl.name == "close"
    assert plot_decl.kind == "plot"
    assert len(result.ir.instrs) > 0
