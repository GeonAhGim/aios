"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 — `runtime/interpreter.py` 테스트.

DSL-3 `parse()` → DSL-7 `lower_program()` 산출물을 그대로 실행한다. 확인 항목:
(1) decl별 결과(let/signal/plot/order)와 §3.3 의미론, (2) 빌트인은 주입된
레지스트리로만 디스패치되고 반환값은 주석·봉 수와 대조됨(스텁 없음), (3) 입력
계약(시리즈 입력 필수·미선언 입력 거부·리터럴 기본값·타입 정합), (4) DoD
"재귀·I/O 없음" — 깊은 IR을 낮은 재귀 한도에서 실행하고, 모듈 import·자기호출을
AST로 정적 검사, (5) 결정론(재실행·바이트 왕복 동일). 참조 구현 대비 동일성은
`test_interpreter_property.py`.
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path
from typing import cast

import pytest

import src.core.script.runtime.interpreter as interpreter_module
from src.core.script.grammar.ast import Identifier, NumberLiteral
from src.core.script.grammar.parser import parse
from src.core.script.ir import (
    BinOp,
    ConstFloat,
    DeclareInput,
    IRProgram,
    IRStackError,
    Load,
    Store,
    from_bytes,
    lower_program,
    to_bytes,
)
from src.core.script.runtime import (
    BuiltinRegistry,
    CallSite,
    ScriptRuntimeError,
    Series,
    Value,
    broadcast,
    execute,
)
from tests.conftest import paused_coverage

_RUNTIME_DIR = Path(__file__).resolve().parents[4] / "src" / "core" / "script" / "runtime"

SAMPLE = (
    "input close: series<float> = 0\n"
    "input length: int = 3\n"
    "input k: float = 2\n"
    "let prev = close[1]\n"
    "let doubled = close * k\n"
    "let up = close > prev\n"
    "signal go_long = up and close crosses_above k\n"
    "plot(doubled, 1)\n"
    "order(buy, length, 7) when go_long"
)
CLOSE = Series.of_floats([1, 3, 2, 5, 1])


# ---- 실행 결과 ----


def test_sample_program_executes_with_documented_semantics() -> None:
    result = execute(lower_program(parse(SAMPLE)), bar_count=5, inputs={"close": CLOSE})
    assert result.bar_count == 5
    assert result.bindings["length"] == 3
    assert result.bindings["k"] == 2.0
    assert result.bindings["prev"] == Series((None, 1.0, 3.0, 2.0, 5.0))
    assert result.bindings["doubled"] == Series((2.0, 6.0, 4.0, 10.0, 2.0))
    assert result.bindings["up"] == Series((None, True, False, True, False))
    assert result.signals == {"go_long": Series((None, True, False, True, False))}
    assert result.bindings["go_long"] == result.signals["go_long"]
    plot = result.plots[0]
    assert plot.value == result.bindings["doubled"] and plot.type == "series<float>"
    assert plot.style == NumberLiteral(value=1)
    order = result.orders[0]
    assert order.when == result.signals["go_long"]
    assert order.side == Identifier(name="buy")
    assert order.qty_expr == Identifier(name="length")
    assert order.opts == NumberLiteral(value=7)


def test_scalar_only_program_stays_scalar_and_int_division_truncates() -> None:
    src = "input a: int = 7\nlet q = a / 2\nlet f = q * 1.5\nsignal s = q < f"
    result = execute(lower_program(parse(src)), bar_count=0)
    assert result.bindings["q"] == 3
    assert result.bindings["f"] == 4.5
    assert result.signals["s"] is True


def test_host_inputs_override_literal_defaults_and_are_type_checked() -> None:
    src = "input length: int = 3\ninput k: float = 1\nlet x = k * length"
    result = execute(lower_program(parse(src)), bar_count=0, inputs={"length": 5, "k": 2})
    assert result.bindings["x"] == 10.0  # k=2 는 float 주석 → 2.0 승격
    with pytest.raises(ScriptRuntimeError):
        execute(lower_program(parse(src)), bar_count=0, inputs={"length": 2.5})
    with pytest.raises(ScriptRuntimeError):
        execute(lower_program(parse(src)), bar_count=0, inputs={"length": True})


def test_series_input_must_be_supplied_and_match_bar_count() -> None:
    ir = lower_program(parse("input close: series<float> = 0\nlet x = close[1]"))
    with pytest.raises(ScriptRuntimeError, match="공급"):
        execute(ir, bar_count=5)
    with pytest.raises(ScriptRuntimeError, match="길이"):
        execute(ir, bar_count=4, inputs={"close": CLOSE})
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=5, inputs={"close": 1.0})
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=5, inputs={"close": Series.of_bools([True] * 5)})


def test_undeclared_input_name_is_rejected() -> None:
    ir = lower_program(parse("input k: float = 1"))
    with pytest.raises(ScriptRuntimeError, match="선언되지 않은"):
        execute(ir, bar_count=0, inputs={"typo": 1.0})


def test_bar_count_must_be_non_negative_int() -> None:
    ir = lower_program(parse("input k: float = 1"))
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=-1)
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=True)


def test_literal_default_that_contradicts_declared_type_is_rejected() -> None:
    ir = lower_program(parse("input n: int = 1.5"))
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=0)


# ---- 빌트인 디스패치(레지스트리 주입, 스텁 없음) ----


def _sma(args: tuple[Value, ...], site: CallSite) -> Value:
    src, n = broadcast(args[0], site.bar_count).values, args[1]
    assert isinstance(n, int)
    out: list[float | None] = []
    for t in range(site.bar_count):
        window = src[max(0, t - n + 1) : t + 1]
        ok = t >= n - 1 and all(isinstance(v, float) for v in window)
        out.append(sum(cast(list[float], list(window))) / n if ok else None)
    return Series.of_floats(out)


def test_unregistered_builtin_is_an_error_not_a_stub() -> None:
    ir = lower_program(parse("input close: series<float> = 0\nlet m = ta.sma(close, 2)"))
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        execute(ir, bar_count=5, inputs={"close": CLOSE})
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins={("math", "sma"): _sma})


def test_registered_builtin_receives_args_in_order_and_call_site() -> None:
    seen: list[tuple[tuple[Value, ...], CallSite]] = []

    def spy(args: tuple[Value, ...], site: CallSite) -> Value:
        seen.append((args, site))
        return _sma(args, site)

    registry: BuiltinRegistry = {("ta", "sma"): spy}
    src = "input close: series<float> = 0\nlet m = ta.sma(close, 2)\nlet p = m[1]"
    ir = lower_program(parse(src))
    result = execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=registry)
    assert seen[0][0] == (CLOSE, 2)
    assert seen[0][1] == CallSite("ta", "sma", "series<float>", 5)
    assert result.bindings["m"] == Series((None, 2.0, 2.5, 3.5, 3.0))
    assert result.bindings["p"] == Series((None, None, 2.0, 2.5, 3.5))


def test_builtin_return_value_is_checked_against_annotation_and_bar_count() -> None:
    ir = lower_program(parse("input close: series<float> = 0\nlet m = ta.sma(close, 2)"))
    bad_shape: BuiltinRegistry = {("ta", "sma"): lambda _a, _s: 1.0}
    with pytest.raises(ScriptRuntimeError, match="스칼라"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=bad_shape)
    bad_len: BuiltinRegistry = {("ta", "sma"): lambda _a, _s: Series.of_floats([1.0])}
    with pytest.raises(ScriptRuntimeError, match="길이"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=bad_len)
    bad_domain: BuiltinRegistry = {("ta", "sma"): lambda _a, _s: Series.of_bools([True] * 5)}
    with pytest.raises(ScriptRuntimeError, match="도메인"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=bad_domain)


def test_indexed_value_can_feed_a_builtin_even_though_static_type_is_scalar() -> None:
    ir = lower_program(parse("input close: series<float> = 0\nlet m = ta.sma(close[1], 2)"))
    result = execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins={("ta", "sma"): _sma})
    assert result.bindings["m"] == Series((None, None, 2.0, 2.5, 3.5))


# ---- 결정론 ----


def test_rerun_and_bytes_round_trip_give_equal_results() -> None:
    ir = lower_program(parse(SAMPLE))
    first = execute(ir, bar_count=5, inputs={"close": CLOSE})
    second = execute(from_bytes(to_bytes(ir)), bar_count=5, inputs={"close": CLOSE})
    assert first == second


def test_malformed_ir_is_rejected_before_execution() -> None:
    ir = IRProgram(instrs=(Store(name="x", type="int"),))
    with pytest.raises(IRStackError):
        execute(ir, bar_count=0)
    unbound = IRProgram(instrs=(Load(name="ghost", type="int"), Store(name="x", type="int")))
    with pytest.raises(ScriptRuntimeError, match="바인딩되지 않은"):
        execute(unbound, bar_count=0)


# ---- DoD: 재귀·I/O 없음 ----


def _frame_depth() -> int:
    depth, frame = 0, sys._getframe()  # noqa: SLF001
    while frame is not None:
        depth, frame = depth + 1, frame.f_back
    return depth


def test_deep_expression_runs_under_tiny_recursion_limit() -> None:
    chain: list[object] = [
        DeclareInput(name="c", type="series<float>", value=0),
        Load(name="c", type="series<float>"),
    ]
    for _ in range(5000):
        chain.extend((ConstFloat(value=1.0), BinOp(operator="+", type="series<float>")))
    chain.append(Store(name="x", type="series<float>"))
    ir = IRProgram(instrs=tuple(chain))  # type: ignore[arg-type]
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(_frame_depth() + 40)
    try:
        result = execute(ir, bar_count=2, inputs={"c": Series.of_floats([0, 1])})
    finally:
        sys.setrecursionlimit(limit)
    assert result.bindings["x"] == Series((5000.0, 5001.0))


_ALLOWED_IMPORT_PREFIXES = (
    "__future__",
    "collections.abc",
    "dataclasses",
    "math",
    "typing",
    "src.core.script.",
)


@pytest.mark.parametrize("module", ["series.py", "interpreter.py", "values.py", "__init__.py"])
def test_runtime_modules_import_no_io_and_never_call_themselves(module: str) -> None:
    tree = ast.parse((_RUNTIME_DIR / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert name.startswith(_ALLOWED_IMPORT_PREFIXES), f"{module}: I/O 가능 import {name}"
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
            callee = call.func
            via_super = isinstance(callee, ast.Attribute) and ast.unparse(callee.value) == "super()"
            own = (isinstance(callee, ast.Name) and callee.id == func.name) or (
                isinstance(callee, ast.Attribute) and callee.attr == func.name and not via_super
            )
            assert not own, f"{module}: {func.name}가 자기 자신을 호출(재귀)"
            assert not (isinstance(callee, ast.Name) and callee.id in {"open", "exec", "eval"})


# ---- DEEPEN(task-2917): 실패 주입(빌트인 디스패치 예외) ----


def test_builtin_dispatch_exception_propagates_unmasked_and_leaves_no_residue() -> None:
    """빌트인 본체는 DSL-9 소유이고 인터프리터는 `_call`에서 호출만 중계한다
    (모듈 docstring 19~22행) — 네트워크·거래소 어댑터를 감싼 실제 빌트인이
    인프라 장애(연결 끊김 등)로 예외를 던지는 상황을 시뮬레이션한다. `_call`은
    `fn(args, site)`를 try/except 없이 그대로 호출한다: 그 예외를
    `ScriptRuntimeError`로 감싸 삼키거나 부분 결과를 성공으로 위장해 반환하면
    안 되고, 원래 예외 타입 그대로(가장 fail-closed한 형태) 즉시 전파해야
    한다. 이어서 같은 IR을 정상 레지스트리로 재실행해 이전 실행의 예외가
    인터프리터 전역 상태를 오염시키지 않았음을 확인한다(매 `execute()` 호출은
    새 `_Machine` 인스턴스)."""
    ir = lower_program(parse("input close: series<float> = 0\nlet m = ta.sma(close, 2)"))

    def _flaky(_args: tuple[Value, ...], _site: CallSite) -> Value:
        raise ConnectionError("simulated exchange adapter timeout")

    with pytest.raises(ConnectionError, match="simulated exchange adapter timeout"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins={("ta", "sma"): _flaky})

    result = execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins={("ta", "sma"): _sma})
    assert result.bindings["m"] == Series((None, 2.0, 2.5, 3.5, 3.0))


# ---- DEEPEN(task-2917): 수치 성능 단언(인터프리터 실행 지연) ----


def test_execution_latency_p95_within_backtest_budget_slice() -> None:
    """ADR-2026-09-09-C Decision 1의 백테스트 예산(로컬 기준, 1개월 M1 1심볼
    3초) 중 인터프리터 1회 실행(스택 머신이 명령열을 한 번 훑는 것) 몫을
    하루치 단위(bar_count=1440, 1일치 1분봉)로 쪼개 250ms로 상한한다. 30개
    let 체인을 20회 반복 실행해 p95로 잰다. 파싱·로우어링(DSL-3/DSL-7의
    몫, 각자 성능 단언을 이미 잼)은 루프 밖에서 한 번만 수행해 이중으로
    재지 않는다."""
    lines = [f"let v{i} = v{i - 1} * 1.0001 + 1 - 1" for i in range(1, 30)]
    source = "input close: series<float> = 0\nlet v0 = close\n" + "\n".join(lines)
    ir = lower_program(parse(source))
    bar_count = 1440
    close = Series.of_floats([float(i % 100) for i in range(bar_count)])

    samples = []
    for _ in range(20):
        with paused_coverage():
            start = time.perf_counter()
            execute(ir, bar_count=bar_count, inputs={"close": close})
            samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    budget_sec = 0.25
    print(f"[DSL-8 execute] p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


# ---- DEEPEN(task-2917): 게이트 적색 재현(빌트인 반환값 검사 무력화) ----


def test_builtin_return_check_removal_lets_length_mismatch_through_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_call`의 빌트인 반환값 검사(`check_value(result, instr.type, self._n,
    ...)`, 모듈 docstring "빌트인 반환값도 주석·봉 수와 대조한다")가
    무력화되는 회귀를 먼저 재현한다: 검사를 항등 함수로 바꾸면 길이가 틀린
    반환값(bar_count=5인데 원소 1개짜리 시리즈)이 그대로 바인딩에 들어가
    프로그램이 조용히 "성공"해버린다(레드 — 바로 위 길이 불일치 negative
    테스트가 잡는 그 케이스가 무검사로 통과함). 원래 구현은 `check_value`가
    즉시 `ScriptRuntimeError`로 거부한다(회귀 가드)."""
    ir = lower_program(parse("input close: series<float> = 0\nlet m = ta.sma(close, 2)"))
    bad_len: BuiltinRegistry = {("ta", "sma"): lambda _a, _s: Series.of_floats([1.0])}

    monkeypatch.setattr(interpreter_module, "check_value", lambda v, *_a, **_k: v)
    result = execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=bad_len)
    assert result.bindings["m"] == Series.of_floats(
        [1.0]
    )  # 회귀: 길이 불일치가 무검사로 통과(레드)

    monkeypatch.undo()
    with pytest.raises(ScriptRuntimeError, match="길이"):
        execute(ir, bar_count=5, inputs={"close": CLOSE}, builtins=bad_len)
