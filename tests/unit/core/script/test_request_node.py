"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 — M2-2a
(task-3976, ADR-2026-09-09-B) `request(symbol, timeframe, expr)` 프리미티브.

이 리프는 파서/AST/비용산정까지만 다룬다 — MTF 런타임 평가·미래참조 검출·
패리티는 M2-2b(후속 리프) 몫이다(spec 참조). DoD: (1) request(symbol,
timeframe, expr) 파싱·상수 검증 유닛테스트, (2) 컴파일 300ms 이하 유지,
(3) 동적 인자 negative 테스트.

D2: negative test >=3(symbol 동적 식별자, timeframe 동적 식별자, symbol을
연산식으로 우회), 실패 주입 1(resources.py의 `_Acc.visit`이 모르는 표현식이
RequestExpr.expr 자리로 새어 들어와도 fail-closed), 수치 성능 단언 1(파서+
자원산정 합산이 DSL 컴파일 예산 300ms 안에 크게 머무는지), 게이트 적색
재현 1(RequestExpr.expr 미방문 회귀를 먼저 재현한 뒤 현재 구현이 이를
막음을 대조).
"""
from __future__ import annotations

import time

import pytest

from src.core.script.analysis.resources import (
    ResourceLimits,
    ScriptResourceLimitError,
    check_resources,
    estimate_resources,
)
from src.core.script.grammar.ast import (
    BinaryExpr,
    Identifier,
    LetDecl,
    NumberLiteral,
    OrderDecl,
    Program,
    RequestExpr,
    program_from_dict,
    to_dict,
)
from src.core.script.grammar.lexer import ScriptSyntaxError
from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import ScriptTypeError, check_program, infer_type

_CLOSE = "input close: series<float> = 0\n"

# ---- 파싱: request(symbol, timeframe, expr) 성공 케이스 ----


def test_request_parses_symbol_timeframe_and_expr() -> None:
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close)')
    decl = program.decls[1]
    assert isinstance(decl, LetDecl)
    assert decl.expr == RequestExpr(symbol="BTCUSDT", timeframe="5m", expr=Identifier(name="close"))


def test_request_expr_argument_can_be_compound() -> None:
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "1h", close + 1)')
    decl = program.decls[1]
    assert isinstance(decl, LetDecl)
    assert decl.expr == RequestExpr(
        symbol="BTCUSDT",
        timeframe="1h",
        expr=BinaryExpr(op="+", left=Identifier(name="close"), right=NumberLiteral(value=1)),
    )


def test_request_embeds_inside_larger_expression() -> None:
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close) > close')
    decl = program.decls[1]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(
        op=">",
        left=RequestExpr(symbol="BTCUSDT", timeframe="5m", expr=Identifier(name="close")),
        right=Identifier(name="close"),
    )


def test_request_roundtrips_through_serialization() -> None:
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close)')
    assert program_from_dict(to_dict(program)) == program


def test_request_result_type_is_always_series_float() -> None:
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close)')
    env = check_program(program)
    assert env["r"] == "series<float>"


def test_request_inner_expr_must_be_numeric() -> None:
    source = _CLOSE + "signal go = close > 0\n" + 'let r = request("BTCUSDT", "5m", go)'
    with pytest.raises(ScriptTypeError):
        check_program(parse(source))


# ---- negative: symbol/timeframe은 컴파일 시 상수여야 한다(동적 심볼 금지) ----


def test_request_symbol_must_be_string_literal_not_identifier() -> None:
    source = _CLOSE + 'input sym: series<float> = 0\nlet r = request(sym, "5m", close)'
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(source)
    assert excinfo.value.code == "SCRIPT_SYNTAX"


def test_request_timeframe_must_be_string_literal_not_identifier() -> None:
    source = _CLOSE + 'input tf: series<float> = 0\nlet r = request("BTCUSDT", tf, close)'
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(source)
    assert excinfo.value.code == "SCRIPT_SYNTAX"


def test_request_symbol_cannot_be_computed_expression() -> None:
    # symbol 자리는 문법상 STRING 토큰 하나만 허용한다 — 문자열 연결·연산식
    # 우회 자체가 파서 단계에서 불가능하다(구조로 강제).
    source = _CLOSE + 'let r = request("BTC" + "USDT", "5m", close)'
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(source)
    assert excinfo.value.code == "SCRIPT_SYNTAX"


def test_request_timeframe_cannot_be_numeric_literal() -> None:
    source = _CLOSE + 'let r = request("BTCUSDT", 5, close)'
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(source)
    assert excinfo.value.code == "SCRIPT_SYNTAX"


def test_request_missing_args_is_syntax_error() -> None:
    with pytest.raises(ScriptSyntaxError):
        parse(_CLOSE + 'let r = request("BTCUSDT", "5m")')


# ---- 자원 산정(analysis/resources.py): 요청당 시리즈 비용 ----


def test_resources_counts_request_and_result_is_series() -> None:
    est = check_resources(parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close)'))
    assert est.request_count == 1
    # close(input) + r(request 결과, 항상 series) — request(...)는 항상
    # series<float>로 타입 추론되므로 decl의 series_count 경로로 이미 반영된다
    # (request_count에서 이중 계상하지 않는다).
    assert est.series_count == 2


def test_resources_request_count_boundary() -> None:
    limits = ResourceLimits(max_requests=1)
    one = _CLOSE + 'let r = request("BTCUSDT", "5m", close)'
    assert check_resources(parse(one), limits).request_count == 1
    two = one + '\nlet r2 = request("ETHUSDT", "15m", close)'
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(parse(two), limits)
    assert excinfo.value.metric == "request_count"


# ---- 실패 주입: RequestExpr.expr 자리로 유니온 밖 표현식이 새는 경우 ----


class _UnknownExpr:
    """DSL-1 `Expr` 유니온 밖의 표현식을 흉내(업스트림 계약 위반). 파서는
    이런 값을 절대 만들지 않는다 — `model_construct`로만 조립 가능하다."""


def test_unrecognized_expr_inside_request_fails_closed() -> None:
    """실패 주입: DSL-4는 `OrderDecl.side`를 형 검사하지 않는다(§3.3에 없는
    필드). 그 자리에 RequestExpr을 실어 안쪽 `expr`에 유니온 밖 표현식을
    끼워 넣으면 `check_program`은 이 노드를 건드리지 않고 통과하고,
    `estimate_resources`의 `_Acc.visit`(RequestExpr → 내부 expr로 전파)이
    마지막 방어선이 된다 — "모르니 통과"가 아니라 fail-closed로 거부됨을
    확인한다(`estimate_resources`는 `check_program`을 다시 타지 않으므로
    손으로 만든 env로 충분하다)."""
    request = RequestExpr.model_construct(
        symbol="BTCUSDT", timeframe="5m", expr=_UnknownExpr()
    )
    order = OrderDecl.model_construct(
        side=request,
        qty_expr=NumberLiteral(value=1),
        opts=None,
        when=Identifier(name="go"),
    )
    program = Program.model_construct(decls=(order,))

    with pytest.raises(ScriptResourceLimitError) as excinfo:
        estimate_resources(program, {"go": "bool"})

    assert excinfo.value.code == "SCRIPT_RESOURCE_LIMIT"


# ---- 게이트 적색 재현: RequestExpr.expr 미방문 회귀 가드 ----


def test_request_inner_expr_visitation_regression_guard() -> None:
    """`_Acc.visit`이 (가상의 회귀판처럼) `RequestExpr.expr`을 방문하지 않으면
    그 안에 숨은 call_depth 초과가 산정에서 빠져 상한 검사가 통과해버린다
    (미탐). 그 결여된 버전을 expr을 얕은 표현식으로 바꾼 프로그램으로 먼저
    재현하고, 실제 구현은 expr을 방문하므로 동일한 깊이의 스크립트를
    정확히 거부함을 대조한다."""
    limits = ResourceLimits(max_call_depth=2)
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", ta.a(ta.b(ta.c(close))))')

    # 게이트 적색 재현: expr을 얕은 표현식으로 바꾼(= 미방문 회귀 흉내) 버전은
    # depth 상한 안에서 통과한다(미탐).
    def _shallowed(decl: LetDecl) -> LetDecl:
        assert isinstance(decl.expr, RequestExpr)
        shallow_request = decl.expr.model_copy(update={"expr": Identifier(name="close")})
        return decl.model_copy(update={"expr": shallow_request})

    def _is_request_let(decl: object) -> bool:
        return isinstance(decl, LetDecl) and isinstance(decl.expr, RequestExpr)

    shallow = program.model_copy(
        update={
            "decls": tuple(
                _shallowed(decl) if _is_request_let(decl) else decl for decl in program.decls
            )
        }
    )
    check_resources(shallow, limits)  # 예외 없이 통과 — 미탐 재현

    # 회귀 가드: 현재 구현은 expr을 방문하므로 동일 스크립트를 정확히 거부한다.
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(program, limits)
    assert excinfo.value.metric == "call_depth"


# ---- 성능 단언: 파서+자원산정 합산이 DSL 컴파일 예산(300ms) 안에 머문다 ----


def test_request_heavy_script_compiles_well_within_budget() -> None:
    """수치 성능 단언(ADR-2026-09-09-C Decision 1, DSL 컴파일 300ms 예산):
    request() 8개(=DEFAULT_LIMITS.max_requests) + 일반 let 50개를 포함한
    스크립트의 parse()+check_resources() 합산 지연이 예산의 절반 안에
    머무는지 30회 반복 p95로 확인한다(request 전용 비용이 예산을 잠식하지
    않음을 조기에 드러낸다)."""
    requests = "\n".join(
        f'let req{i} = request("SYM{i}", "5m", close)' for i in range(8)
    )
    lets = "\n".join(f"let v{i} = ta.sma(close[{i % 5}], {i % 20 + 1})" for i in range(50))
    source = _CLOSE + requests + "\n" + lets
    limits = ResourceLimits(max_requests=8)
    budget_sec = 0.15

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        check_resources(parse(source), limits)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    print(f"[M2-2a request] compile p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


def test_infer_type_direct_call_matches_check_program() -> None:
    """`infer_type`을 `check_program` 없이 직접 호출해도 동일하게
    series<float>를 반환함을 확인(§checker.py `_infer_request` 단위 확인)."""
    program = parse(_CLOSE + 'let r = request("BTCUSDT", "5m", close)')
    decl = program.decls[1]
    assert isinstance(decl, LetDecl)
    env = {"close": "series<float>"}
    assert infer_type(decl.expr, env) == "series<float>"
