"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-15 —
`import_/pine/transpile.py::transpile_and_verify` DEEPEN.

negative test 3건 이상: 불변식 위반 입력을 명시적으로 거부하는 케이스.
실패주입 1건: monkeypatch로 의존성 예외 유발.

DoD: task-9994 (task-6704 고아 산출물 회수 5828 qa-2).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.script.import_.pine.lexer import PineSyntaxError
from src.core.script.import_.pine.transpile import (
    PineTranspileError,
    transpile_and_verify,
)

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


def test_transpile_and_verify_monkeypatch_lexer_raises() -> None:
    """tokenize 함수를 monkeypatch해 예외를 유발하면 전파된다."""
    from src.core.script.import_.pine import parser as parser_module

    def _mock_tokenize(_source: str) -> list[str]:
        raise PineSyntaxError("mock lexer failure", 1, 0)

    with patch.object(parser_module, "tokenize", _mock_tokenize):
        with pytest.raises(PineSyntaxError, match="mock lexer failure"):
            transpile_and_verify("x = 1")


# ---- 긍정 테스트: 정상 변환 + 검증 통과 ----


def test_transpile_and_verify_passes_simple_arithmetic() -> None:
    """정상적인 산술식 변환이 검증 단계를 통과한다."""
    result = transpile_and_verify("x = 1 + 2")
    assert result is not None
    assert result.program is not None
    assert result.ir is not None
    assert len(result.ir_bytes) > 0


def test_transpile_and_verify_passes_ta_call() -> None:
    """ta.sma(...) 변환이 검증 단계를 통과한다."""
    result = transpile_and_verify("sma = ta.sma(close, 20)")
    assert result is not None
    assert result.program is not None
    assert result.ir is not None


def test_transpile_and_verify_passes_strategy_entry() -> None:
    """strategy.entry(...) 변환이 검증 단계를 통과한다."""
    result = transpile_and_verify('strategy.entry("Long", 1, qty=2)')
    assert result is not None
    assert result.program is not None


def test_transpile_and_verify_passes_plot() -> None:
    """plot(...) 문장이 검증 단계를 통과한다."""
    result = transpile_and_verify("plot(close)")
    assert result is not None
    assert result.program is not None
