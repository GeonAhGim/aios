"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-4 —
`typing/types.py` + `typing/checker.py` 테스트.

DoD("시리즈/스칼라 승격·거부")를 두 축으로 확인한다: (1) 선언(input/let/
signal)이 환경에 올바른 타입으로 등록되고 이후 decl에서 재사용되는지,
(2) 표현식 트리 전체(단항·이항·postfix·call)에서 승격이 성립하는 조합은
통과하고 성립하지 않는 조합(bool과 수치의 혼용, 스칼라 인덱싱 등)은
`ScriptTypeError`(`SCRIPT_TYPE`)로 거부되는지. 입력은 항상 DSL-3
`parse()`의 실제 산출물(AST를 손으로 조립하지 않는다)이라 파서·검사기
경계가 실제로 맞물리는지도 함께 검증한다.

DEEPEN(task-2912, 원 task-1354 DEPTH 감사 부족분, docs/audit/DEPTH_DSL_IND.md):
negative는 이미 11건(재선언·미정의 식별자·단항/이항/postfix/call/plot/
order 각각의 타입 위반)으로 충분하다고 판단하고 추가하지 않는다. 새
기능은 추가하지 않고 깊이만 올린다 — 대신 (1) 실패 주입 1건(재귀
한도를 인위적으로 낮춰 `infer_type`의 단항 재귀로 인한 스택 고갈을
결정론적으로 재현하고, 얕은 입력은 여전히 성공하며 깊은 중첩은 조용히
잘못된 타입을 내지 않고 `RecursionError`로 fail-closed함을 확인),
(2) 수치 성능 단언 1건(ADR-2026-09-09-C Decision 1의 DSL 컴파일 예산
300ms 중 파싱 단계가 이미 절반을 쓴다고 가정한 task-2911 DEEPEN에 이어,
타입 검사 단계는 남은 예산의 절반인 전체 예산의 1/4(75ms) 안에 머무는지
확인 — 선형 이상의 회귀를 조기에 드러낸다), (3) 게이트 적색 재현 1건
(`_infer_call`이 참조하는 `NUMERIC_TYPES` 가드가 무력화되면 bool 인자가
조용히 통과하는 레드 상태를 먼저 재현하고, 실장 코드는 그 가드 덕분에
SCRIPT_TYPE으로 fail-closed함을 대조)을 추가한다.
"""

from __future__ import annotations

import sys
import time

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.typing import checker as checker_module
from src.core.script.typing.checker import ScriptTypeError, check_program

# ---- 선언 등록: input/let/signal이 환경에 타입으로 남는다 ----


def test_input_scalar_registers_declared_type() -> None:
    env = check_program(parse("input length: int = 14"))
    assert env == {"length": "int"}


def test_input_series_registers_declared_type() -> None:
    env = check_program(parse("input src: series<float> = 0"))
    assert env == {"src": "series<float>"}


def test_let_infers_type_from_expr() -> None:
    env = check_program(parse("let a = 1\nlet b = a + 2"))
    assert env == {"a": "int", "b": "int"}


def test_signal_registers_bool_family_type() -> None:
    env = check_program(parse("signal go_long = 1 < 2"))
    assert env == {"go_long": "bool"}


def test_redeclaration_is_script_type_error() -> None:
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse("let a = 1\nlet a = 2"))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_undefined_identifier_is_script_type_error() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("let x = y + 1"))


# ---- 단항: 산술 부정은 수치만, 논리 부정은 bool만 ----


def test_unary_minus_on_numeric_passes_through_type() -> None:
    env = check_program(parse("let a = 1\nlet b = -a"))
    assert env["b"] == "int"


def test_unary_minus_on_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = -g"))


def test_not_on_bool_passes_through_type() -> None:
    env = check_program(parse("signal g = not (1 < 2)"))
    assert env["g"] == "bool"


def test_not_on_numeric_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("let bad = not 1"))


# ---- postfix: 시리즈만 인덱싱 가능, 원소 타입으로 투영 ----


def test_postfix_index_on_series_projects_to_element_type() -> None:
    env = check_program(parse("input src: series<float> = 0\nlet a = src[1]"))
    assert env["a"] == "float"


def test_postfix_index_on_scalar_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("input x: int = 1\nlet bad = x[0]"))


# ---- 산술(+·-·*·/): int/float/series<float> 승격, bool과는 거부 ----


def test_arith_int_and_int_stays_int() -> None:
    env = check_program(parse("let a = 1 + 2"))
    assert env["a"] == "int"


def test_arith_int_and_float_promotes_to_float() -> None:
    env = check_program(parse("let a = 1 + 1.5"))
    assert env["a"] == "float"


def test_arith_scalar_and_series_promotes_to_series() -> None:
    env = check_program(parse("input src: series<float> = 0\nlet a = src + 1"))
    assert env["a"] == "series<float>"


def test_arith_with_bool_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = g + 1"))


# ---- 비교(cmp·crosses_*): 수치만, 결과는 시리즈 섞이면 series<bool> ----


def test_cmp_of_scalars_is_bool() -> None:
    env = check_program(parse("signal g = 1 < 2"))
    assert env["g"] == "bool"


def test_cmp_of_series_is_series_bool() -> None:
    env = check_program(parse("input src: series<float> = 0\nsignal g = src > 0"))
    assert env["g"] == "series<bool>"


def test_crosses_above_of_two_series_is_series_bool() -> None:
    env = check_program(
        parse(
            "input a: series<float> = 0\n"
            "input b: series<float> = 0\n"
            "signal cross = a crosses_above b"
        )
    )
    assert env["cross"] == "series<bool>"


def test_cmp_with_bool_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nsignal bad = g < 1"))


# ---- 논리(and·or): bool 계열만, series<bool>이 섞이면 승격 ----


def test_logical_and_of_bools_is_bool() -> None:
    env = check_program(parse("signal g = (1 < 2) and (2 < 3)"))
    assert env["g"] == "bool"


def test_logical_and_promotes_to_series_bool() -> None:
    env = check_program(
        parse(
            "input src: series<float> = 0\n"
            "signal s1 = src > 0\n"
            "signal s2 = src < 100\n"
            "signal both = s1 and s2"
        )
    )
    assert env["both"] == "series<bool>"


def test_logical_and_with_numeric_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal bad = 1 and 2"))


# ---- call(ns.ident): 인자는 수치만, 시리즈가 하나라도 있으면 series<float> ----


def test_call_all_scalar_args_returns_float() -> None:
    env = check_program(parse("let x = math.pi()"))
    assert env["x"] == "float"


def test_call_with_series_arg_returns_series_float() -> None:
    env = check_program(parse("input close: series<float> = 0\nlet rsi_val = ta.rsi(close, 14)"))
    assert env["rsi_val"] == "series<float>"


def test_call_with_bool_arg_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = ta.sma(g, 14)"))


# ---- plot/order: 각자의 위치 제약 ----


def test_plot_of_numeric_passes() -> None:
    check_program(parse("input src: series<float> = 0\nplot(src)"))


def test_plot_of_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal go = 1 < 2\nplot(go)"))


def test_order_when_bool_passes_and_side_qty_are_not_type_checked() -> None:
    """side/qty_expr는 §3.3에 별도 프로덕션이 없는 opaque Expr이라 검사
    대상이 아니다 — `buy`를 어디에도 선언하지 않아도 통과해야 한다."""
    check_program(parse("order(buy, 1) when 1 < 2"))


def test_order_when_non_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("order(buy, 1) when 1 + 2"))


# ---- 통합: DSL-3 round-trip 테스트와 같은 형태의 스크립트 전체가 통과 ----


def test_full_program_type_checks_end_to_end() -> None:
    source = (
        "input length: int = 14\n"
        "input close: series<float> = 0\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "let prev = close[1]\n"
        "signal go_long = rsi_val < 30 and close > prev\n"
        "plot(rsi_val)\n"
        "order(buy, 1) when go_long"
    )
    env = check_program(parse(source))
    assert env == {
        "length": "int",
        "close": "series<float>",
        "rsi_val": "series<float>",
        "prev": "float",
        "go_long": "series<bool>",
    }


# ---- DEEPEN(task-2912): 실패 주입(검사기 재귀 한도/스택 고갈 시뮬레이션) ----


def _current_frame_depth() -> int:
    depth, frame = 0, sys._getframe()  # noqa: SLF001
    while frame is not None:
        depth, frame = depth + 1, frame.f_back
    return depth


def test_deeply_nested_unary_fails_closed_under_low_recursion_limit() -> None:
    """`infer_type`은 `UnaryExpr`마다 자기 자신을 재귀 호출한다(checker.py
    자체에 깊이 상한이 없다). 이 리프는 새 기능을 추가하지 않으므로, 실제
    스택 고갈을 재현하려면 재귀 한도를 인위적으로 낮춰 결정론적으로
    시뮬레이션해야 한다. 깊게 중첩된 단항식은 먼저 기본 재귀 한도에서
    정상적으로 파싱해 두고(파서 자체의 재귀 한도는 DSL-3 DEEPEN에서 이미
    검증됨), 그 다음 검사 단계에서만 한도를 낮춰 (1) 한도를 낮춘 상태에서도
    얕은 정상 프로그램은 여전히 통과하고, (2) 이미 만들어진 깊은 단항
    AST를 검사할 때는 조용히 잘못된 타입을 돌려주거나 멈추지 않고 곧바로
    `RecursionError`로 fail-closed함을 확인한다."""
    deep_source = "let x = 1\nlet y = " + "-" * 200 + "x"
    deep_program = parse(deep_source)

    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(_current_frame_depth() + 60)
    try:
        shallow_env = check_program(parse("let x = 1\nlet y = --x"))
        assert shallow_env["y"] == "int"

        with pytest.raises(RecursionError):
            check_program(deep_program)
    finally:
        sys.setrecursionlimit(limit)


# ---- DEEPEN(task-2912): 수치 성능 단언(타입 검사 지연) ----


def test_check_program_latency_stays_within_quarter_of_dsl_compile_budget() -> None:
    """ADR-2026-09-09-C Decision 1의 DSL 컴파일 예산은(로컬 기준) 300ms다.
    파싱 단계가 이미 절반(150ms, task-2911 DEEPEN)을 쓴다고 가정하면 타입
    검사 단계는 남은 예산의 절반, 즉 전체 예산의 1/4(75ms) 안에 머물러야
    한다 — 토큰화 없이 이미 만들어진 AST를 한 번 훑기만 하므로 파싱보다
    가벼워야 정상이다. arith/postfix/call/cmp/and 계층을 모두 섞어 서로
    연쇄 참조하는 1000개 decl짜리 스크립트로 확인한다(파싱 자체는 시간
    측정에서 제외 — 이 단언은 검사기만의 지연을 잰다)."""
    lines: list[str] = []
    for i in range(1, 499):
        lines.append(f"let v{i} = ta.rsi(v{i - 1}[1], length) + v{i - 1} * 2 - 1")
        lines.append(f"let b{i} = (v{i} > 0) and b{i - 1}")
    source = (
        "input length: int = 14\n"
        "input close: series<float> = 0\n"
        "let v0 = close\n"
        "let b0 = close > 0\n" + "\n".join(lines)
    )
    program = parse(source)

    start = time.perf_counter()
    env = check_program(program)
    elapsed = time.perf_counter() - start

    assert len(env) == 1000
    assert elapsed < 0.075


# ---- DEEPEN(task-2912): 게이트 적색 재현(호출 인자 수치 계열 가드) ----


def test_call_arg_numeric_guard_prevents_script_type_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_infer_call`의 `if t not in NUMERIC_TYPES` 가드가 무력화되는 회귀
    (예: `types.NUMERIC_TYPES`가 실수로 bool까지 포함하도록 확장됨)가
    나면 무슨 일이 일어나는지 먼저 재현한다: 가드가 참조하는
    `NUMERIC_TYPES` 자체를 bool까지 포함하도록 넓히면 `ta.sma(bool 신호,
    ...)`처럼 DoD가 "거부"라고 못박은 조합도 조용히 통과한다(레드).
    실장 코드는 `NUMERIC_TYPES`가 온전한 한 동일 입력을 SCRIPT_TYPE으로
    fail-closed한다. 이 가드가 다시 약화되면 첫 번째 단언(레드 재현)이
    아니라 두 번째 단언(정상 거부 확인)이 실패해 이 테스트가 레드가
    된다."""
    source = "signal g = 1 < 2\nlet bad = ta.sma(g, 14)"

    monkeypatch.setattr(
        checker_module,
        "NUMERIC_TYPES",
        frozenset({"int", "float", "series<float>", "bool"}),
    )
    regressed = check_program(parse(source))
    assert regressed["bad"] == "float"

    monkeypatch.undo()
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse(source))
    assert excinfo.value.code == "SCRIPT_TYPE"
