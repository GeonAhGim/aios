"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-3 —
`grammar/parser.py` DEEPEN 테스트 (task-2911).

실패 주입, 수치 성능 단언, 게이트 적색 재현 3축을 담당한다.
원본 `test_parser.py`의 DEEPEN 섹션을 분할했다.
"""

from __future__ import annotations

import sys

import pytest

from src.core.script.grammar import parser_expr as parser_expr_module
from src.core.script.grammar.ast import (
    CallExpr,
    LetDecl,
    NumberLiteral,
)
from src.core.script.grammar.lexer import ScriptSyntaxError
from src.core.script.grammar.parser import parse
from tests._perf.relative_budget import RelativeBudget

# ---- DEEPEN(task-2911): 실패 주입(파서 재귀 한도/스택 고갈 시뮬레이션) ----


def _current_frame_depth() -> int:
    depth, frame = 0, sys._getframe()  # noqa: SLF001
    while frame is not None:
        depth, frame = depth + 1, frame.f_back
    return depth


def test_deeply_nested_parens_fail_closed_under_low_recursion_limit() -> None:
    """`_primary`의 `"(" expr ")"` 분기는 중첩마다 `_expr` 전체 체인
    (`_and_expr → ... → _primary`)을 다시 타는 상호재귀다. 파서에는 깊이
    상한이 없고 이 리프는 새 기능을 추가하지 않으므로, 실제 스택 고갈을
    흉내내려면 재귀 한도를 인위적으로 낮춰 결정론적으로 재현해야 한다.
    이 테스트는 (1) 한도를 낮춘 상태에서도 얕은 정상 입력은 여전히
    성공하고, (2) 한도를 넘는 깊은 중첩은 조용히 잘못된 AST를 돌려주거나
    멈추지 않고 곧바로 `RecursionError`로 fail-closed함을 확인한다."""
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(_current_frame_depth() + 60)
    try:
        shallow = parse("let x = ((1))")
        assert shallow.decls == (LetDecl(name="x", expr=NumberLiteral(value=1)),)

        deep_source = "let x = " + "(" * 500 + "1" + ")" * 500
        with pytest.raises(RecursionError):
            parse(deep_source)
    finally:
        sys.setrecursionlimit(limit)


# ---- DEEPEN(task-2911): 수치 성능 단언(파싱 지연) ----


@pytest.mark.perf
def test_parse_latency_stays_within_half_of_dsl_compile_budget() -> None:
    """ADR-2026-09-09-C Decision 1의 DSL 컴파일 예산은(로컬 기준) 300ms다.
    파서는 그 파이프라인의 한 단계일 뿐이므로 예산 전체를 단독으로 써서는
    안 된다. or/and/cmp/arith/term/postfix/call 계층을 모두 섞은 1000개
    decl짜리 스크립트를 파싱해, 파서 단계 지연이 예산의 절반 안에 머무름을
    확인한다 — 선형 이상(이차 이상)의 성능 저하를 조기에 드러낸다.

    task-7434가 옮긴 process_time 기반 절대 150ms `perf_budget`도 CI 러너
    클록 속도에 매여 느린 러너에서 반복 적색이었다(task-7631). `RelativeBudget`
    으로 옮겨 같은 프로세스 calibration 루프 대비 배수(기존 예산/calibration
    ≈1.6배)로 표현한다. esc-ci-pytest_latency_serial(재발, task-8851): 8코어
    포화 경합 재현에서 기본 n=5/calibration_n=3은 `process_time()`의 Windows
    ~15.6ms 클록 틱 양자화로 ratio가 급등해 5회 중 1회 실패했다 —
    `max_ratio`(ADR-2026-09-09-C 결정치)는 그대로 두고 best-of 표본 수만
    늘려(5->9, 3->7) 경합 없는 순간을 잡을 확률을 높인다(같은 재현에서 3회
    연속 green 확인 후 반영). best-of-N은 확률적 완화일 뿐 임의 극한 부하의
    보장은 아니다."""
    lines = [
        f"let v{i} = ta.rsi(close[{i % 5}], 14) + v{i - 1} * 2 - 1 and v{i - 1} > 0"
        for i in range(1, 1000)
    ]
    source = "let v0 = close\n" + "\n".join(lines)
    program = None

    def _run_once() -> None:
        nonlocal program
        program = parse(source)

    RelativeBudget().assert_within(
        _run_once, max_ratio=1.6, mode="cpu", n=9, calibration_n=7, label="1000-decl parse"
    )
    assert len(program.decls) == 1000


# ---- DEEPEN(task-2911): 게이트 적색 재현(SCRIPT_SYNTAX 회귀 방지) ----


def test_namespace_whitelist_guard_prevents_script_syntax_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_call`의 `if ns_tok.value not in _NAMESPACES` 화이트리스트 검사가
    무력화되는 회귀(예: `_NAMESPACES`가 실수로 확장됨)가 나면 무슨 일이
    일어나는지 먼저 재현한다: 검사를 무력화하면 ta/math/series가 아닌
    임의 네임스페이스도 조용히 성공적인 `Program`으로 파싱된다(레드).
    실장 코드는 `_NAMESPACES`가 온전한 한 동일 입력을 SCRIPT_SYNTAX로
    fail-closed한다. 이 화이트리스트가 다시 약화되면 첫 번째 단언(레드
    재현)이 아니라 두 번째 단언(정상 동작 확인)이 실패해 이 테스트가
    레드가 된다."""
    source = "let x = evil.exec(1)"

    monkeypatch.setattr(
        parser_expr_module, "_NAMESPACES", frozenset({"ta", "math", "series", "evil"})
    )
    regressed = parse(source)
    decl = regressed.decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == CallExpr(ns="evil", ident="exec", args=(NumberLiteral(value=1),))

    monkeypatch.undo()
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(source)
    assert excinfo.value.code == "SCRIPT_SYNTAX"
