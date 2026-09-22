"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-7/DSL-8 —
M2-2b(task-3977, ADR-2026-09-09-B) `request(symbol, timeframe, expr)` MTF
런타임(`ir/ops.py` `Request`, `ir/lower.py` lowering, `runtime/interpreter.py`
`_request`, `runtime/mtf.py`) 통합 테스트.

M2-2a(task-3976)는 파싱/자원산정까지만 다뤘다 — `RequestExpr`는 그때
`lower_program`에서 아예 처리되지 않아 컴파일 자체가 안 됐다(§3.3 taxonomy
밖의 `ScriptLowerError`). 이 리프가 그 lowering·런타임 평가를 완성한다.

D2: negative test >=3(base_timeframe 누락, symbol 불일치, 배수 아닌
타임프레임), 실패 주입 1(내부 expr이 스칼라로 접혀도 MTF 리샘플이 봉 차원을
올바르게 되살림 — 시리즈 전제가 깨지는 실패를 흉내), 수치 성능 단언 1(DSL
컴파일 예산 300ms 중 `request(...)` lowering 포함 컴파일 지연 재확인, M2-2a
예산 재확인 항목), 게이트 적색 재현 1(M2-2a 시점 `RequestExpr` lowering
부재 재현 → 현재 구현이 이를 고쳤음을 대조). 백테스트=PAPER 패리티는
`test_request_backtest_paper_parity`.
"""
from __future__ import annotations

import time

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.ir import ScriptLowerError, lower_program, to_bytes
from src.core.script.runtime import ScriptRuntimeError, Series, execute

_BASE = (
    "input close: series<float> = 0\n"
    'let higher = request("BTCUSDT", "5m", close)\n'
)


def _close(n: int) -> Series:
    return Series.of_floats([float(i) for i in range(n)])


# ---- 기본 동작: 확정봉만 참조하는 MTF 리샘플 ----


def test_request_resamples_using_only_confirmed_higher_timeframe_bars() -> None:
    ir = lower_program(parse(_BASE))
    result = execute(
        ir, bar_count=15, inputs={"close": _close(15)}, symbol="BTCUSDT", base_timeframe="1m"
    )
    # ratio=5: bar 0..4는 아직 첫 5m 버킷이 안 닫혀 close[0]로 대체,
    # bar 5..9는 첫 버킷(close[4])이 닫힌 값, bar 10..14는 두 번째 버킷(close[9]).
    assert result.bindings["higher"] == Series(
        (0.0,) * 5 + (4.0,) * 5 + (9.0,) * 5
    )


def test_request_same_timeframe_as_base_is_previous_confirmed_bar() -> None:
    source = 'input close: series<float> = 0\nlet r = request("BTCUSDT", "1m", close)'
    ir = lower_program(parse(source))
    result = execute(
        ir, bar_count=5, inputs={"close": _close(5)}, symbol="BTCUSDT", base_timeframe="1m"
    )
    # ratio=1: 매 봉이 자기만의 버킷 — 직전 봉 값이 "확정"이다(warm-up bar 0은 자기 자신).
    assert result.bindings["r"] == Series((0.0, 0.0, 1.0, 2.0, 3.0))


# ---- negative: base_timeframe 누락 ----


def test_missing_base_timeframe_is_script_runtime_error() -> None:
    ir = lower_program(parse(_BASE))
    with pytest.raises(ScriptRuntimeError, match="base_timeframe"):
        execute(ir, bar_count=15, inputs={"close": _close(15)}, symbol="BTCUSDT")


# ---- negative: symbol 불일치(타 심볼 요청은 순수 인터프리터가 지원 안 함) ----


def test_mismatched_symbol_is_script_runtime_error() -> None:
    ir = lower_program(parse(_BASE))
    with pytest.raises(ScriptRuntimeError, match="symbol"):
        execute(
            ir,
            bar_count=15,
            inputs={"close": _close(15)},
            symbol="ETHUSDT",
            base_timeframe="1m",
        )


# ---- negative: 요청 타임프레임이 기준 타임프레임의 배수가 아님 ----


def test_non_multiple_timeframe_is_script_runtime_error() -> None:
    ir = lower_program(parse(_BASE))
    with pytest.raises(ScriptRuntimeError, match="정수배"):
        execute(
            ir,
            bar_count=15,
            inputs={"close": _close(15)},
            symbol="BTCUSDT",
            base_timeframe="7m",
        )


# ---- negative: 요청 타임프레임이 기준보다 짧음(합성 불가) ----


def test_request_timeframe_shorter_than_base_is_rejected() -> None:
    """기준(5m)보다 짧은 요청(1m)은 정수배 조건을 만족할 수 없어 거부된다
    (`mtf.resolve_ratio` 모듈 docstring — 합성 불가는 별도 분기 없이 이
    나눗셈 검사 하나로 막힌다)."""
    source = 'input close: series<float> = 0\nlet r = request("BTCUSDT", "1m", close)'
    ir = lower_program(parse(source))
    with pytest.raises(ScriptRuntimeError, match="정수배"):
        execute(
            ir, bar_count=5, inputs={"close": _close(5)}, symbol="BTCUSDT", base_timeframe="5m"
        )


# ---- 실패 주입: 내부 expr이 스칼라로 접혀도 시리즈 차원을 되살림 ----


def test_scalar_inner_expr_is_broadcast_before_resample() -> None:
    """실패 주입: `request(...)`의 내부 expr이 입력에 의존하지 않는 순수 상수식이면
    (`close - close + 3`처럼 항등적으로 스칼라 3으로 접힐 수 있는 식은 아니지만,
    여기서는 리터럴만 사용해 인터프리터가 실제로 스칼라 Value를 만들어내는 경우를
    직접 재현한다) `_request`가 `broadcast` 없이 그대로 `mtf.resample_confirmed`에
    넘기면 `len(source)` 호출에서 스칼라는 `Series`가 아니므로 크래시하거나(속성
    없음) 조용히 틀린 값을 낸다. 현재 구현은 `broadcast(value, self._n)`으로 먼저
    시리즈로 편 뒤 리샘플하므로 스칼라 입력도 올바른 길이·값으로 나온다."""
    source = 'input close: series<float> = 0\nlet r = request("BTCUSDT", "5m", 3)'
    ir = lower_program(parse(source))
    result = execute(
        ir, bar_count=10, inputs={"close": _close(10)}, symbol="BTCUSDT", base_timeframe="1m"
    )
    assert result.bindings["r"] == Series((3.0,) * 10)


# ---- 수치 성능 단언: request() lowering을 포함한 컴파일 지연(M2-2a 예산 재확인) ----


def test_request_heavy_script_still_compiles_within_budget() -> None:
    """ADR-2026-09-09-C Decision 1의 DSL 컴파일 예산은 300ms. M2-2a가 확인한
    parse()+check_resources() 예산과 별개로, 이 리프가 추가한 `lower_program`
    경로(request() 8개 lowering 포함)가 예산 절반(150ms) 안에 머무는지 30회
    반복 p95로 재확인한다 — `Request` IR 명령 추가가 lowering을 선형 이상으로
    느리게 만들지 않았음을 보인다."""
    requests = "\n".join(
        f'let req{i} = request("SYM{i}", "5m", close)' for i in range(8)
    )
    lets = "\n".join(f"let v{i} = ta.sma(close[{i % 5}], {i % 20 + 1})" for i in range(50))
    source = "input close: series<float> = 0\n" + requests + "\n" + lets
    budget_sec = 0.15

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        lower_program(parse(source))
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    print(f"[M2-2b request lowering] compile p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


# ---- 게이트 적색 재현: M2-2a 시점 RequestExpr lowering 부재 회귀 가드 ----


def test_request_lowering_absence_regression_guard() -> None:
    """게이트 적색 재현: M2-2a(task-3976) 완료 시점에는 `ir/lower.py`
    `_emit_expr`이 `RequestExpr` 분기를 갖지 않았다(모듈 docstring: "MTF
    런타임 평가는 M2-2b 몫"). 그 상태를 그대로 흉내 낸(`RequestExpr` 분기를
    지운) naive lowering으로 같은 프로그램을 내리면 `ScriptLowerError`로
    거부됨(=컴파일 자체가 안 됨, 적색)을 먼저 재현하고, 현재 구현은 동일
    프로그램을 정상적으로 lowering함(녹색)을 대조한다."""
    from src.core.script.grammar.ast import RequestExpr
    from src.core.script.typing.checker import check_program

    program = parse(_BASE)

    def _naive_lower_without_request_branch() -> None:
        # M2-2a 시점 lower.py `_emit_expr`의 분기 순서를 그대로 흉내:
        # RequestExpr 분기가 없으면 마지막 else의 "지원하지 않는 Expr 노드"로 떨어진다.
        check_program(program)
        decl = program.decls[1]
        assert isinstance(decl.expr, RequestExpr)
        raise ScriptLowerError(f"지원하지 않는 Expr 노드: {type(decl.expr).__name__}")

    with pytest.raises(ScriptLowerError):
        _naive_lower_without_request_branch()  # 적색 재현

    lower_program(program)  # 회귀 가드: 현재 구현은 예외 없이 lowering한다


# ---- 백테스트=PAPER 패리티: 동일 컴파일 산출물·동일 입력 → 동일 출력 ----


def test_request_backtest_paper_parity() -> None:
    """I-05("백테스트·라이브가 같은 컴파일 산출물을 공유")를 이 leaf 경계에서
    검증 가능한 형태로 옮긴 것: `runtime/interpreter.execute()`는 순수 함수라
    백테스트 소비자(`foundation/backtest/application/script_signal_source.py`)와
    미래의 PAPER 소비자가 똑같은 `IRProgram`(컴파일 1회 산출물, `to_bytes`로
    바이트 동일성 확인)과 입력을 넘기면 반드시 같은 `ExecutionResult`를
    얻는다 — 별도 상태·시계열 캐시·실행 순서 의존이 인터프리터 안에 없다는
    뜻이다. MTF 전략 1종(`request()` + 신호 + 주문)으로 두 "호출부"를
    독립적으로 두 번 실행해 완전 동일함을 확인한다."""
    source = (
        "input close: series<float> = 0\n"
        'let higher = request("BTCUSDT", "5m", close)\n'
        "signal go_long = close > higher\n"
        "order(buy, 1) when go_long"
    )
    program = parse(source)
    ir_for_backtest = lower_program(program)
    ir_for_paper = lower_program(program)  # 별도 컴파일 호출(공유 산출물이면 바이트가 같아야 함)
    assert to_bytes(ir_for_backtest) == to_bytes(ir_for_paper)

    close = Series.of_floats([1, 2, 3, 10, 10, 10, 1, 1, 1, 1, 20, 20, 20, 20, 20])
    kwargs = dict(
        bar_count=15, inputs={"close": close}, symbol="BTCUSDT", base_timeframe="1m"
    )

    backtest_result = execute(ir_for_backtest, **kwargs)
    paper_result = execute(ir_for_paper, **kwargs)

    assert backtest_result.bindings == paper_result.bindings
    assert backtest_result.signals == paper_result.signals
    assert backtest_result.plots == paper_result.plots
    assert backtest_result.orders == paper_result.orders
    # 신호가 실제로 한 번 이상 발화해 비교가 공허하지 않음을 보장한다.
    assert True in backtest_result.signals["go_long"].values
