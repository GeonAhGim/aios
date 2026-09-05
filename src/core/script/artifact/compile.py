"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-12 —
AIOS Script 컴파일 파이프라인 조립(순수, I/O 없음).

DSL-2 렉서 → DSL-3 파서 → DSL-4 타입 → DSL-5 미래참조 → DSL-6 자원 →
DSL-7 IR 순서 그대로 호출한다(task-1535 spec 문구). 각 단계는 재구현하지
않고 그 단계의 공개 함수만 부른다. 성공하면 `CompiledScript`(IR·IR 바이트·
자원 산정치·`script_hash`)를, 실패하면 §3.3 taxonomy 4종(`SCRIPT_SYNTAX`·
`SCRIPT_TYPE`·`SCRIPT_LOOKAHEAD`·`SCRIPT_RESOURCE_LIMIT`)을 하나의
`ScriptCompileError`로 감싸 (line, col)과 함께 낸다.

오류 위치 복원(§3.3 "위치 정보 포함"): 렉서·파서·lookahead 오류는 토큰
위치를 이미 갖는다. 타입·자원 오류는 AST에 위치가 없어(DSL-1 decision)
"선언 접두(prefix) 이분탐색"으로 복원한다 — 타입 검사는 선언을 소스 순서로
훑고 첫 오류에서 멈추며, 자원 산정치는 모두 선언이 늘수록 단조 증가하므로
`Program(decls[:k])`가 실패하는 최소 k의 k번째 선언이 원인이다. 선언은
문법상 반드시 `input|let|plot|signal|order` 키워드로 시작하고 그 키워드는
표현식 안에 올 수 없으므로 k번째 선언 시작 키워드 토큰의 (line, col)이
곧 선언 위치다.

단계 순서의 결과: 음수·변수 postfix 인덱스는 파서(DSL-3)가 lookahead보다
먼저 `SCRIPT_SYNTAX`로 거부한다. `SCRIPT_LOOKAHEAD`는 파서를 통과하는
형태(`ta.security(...)`류)에서 난다. `ScriptLowerError`(DSL-7)는 taxonomy
밖 — 타입검사를 통과한 §3.3 AST는 항상 내려가야 하므로 계약 위반이며,
여기서 감싸지 않고 그대로 전파한다(API 계층에서 500 INTERNAL_ERROR).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_lookahead
from src.core.script.analysis.resources import (
    DEFAULT_LIMITS,
    ResourceEstimate,
    ResourceLimits,
    ScriptResourceLimitError,
    check_resources,
)
from src.core.script.artifact.hash import script_hash
from src.core.script.grammar.ast import GRAMMAR_VERSION, Program
from src.core.script.grammar.lexer import ScriptSyntaxError, Token, TokenKind, tokenize
from src.core.script.grammar.parser import parse
from src.core.script.ir.lower import lower_program
from src.core.script.ir.ops import IR_VERSION, IRProgram, to_bytes
from src.core.script.typing.checker import ScriptTypeError, check_program

SCRIPT_ERROR_CODES: Final = frozenset(
    {"SCRIPT_SYNTAX", "SCRIPT_TYPE", "SCRIPT_LOOKAHEAD", "SCRIPT_RESOURCE_LIMIT"}
)
_DECL_KEYWORDS: Final = frozenset({"input", "let", "plot", "signal", "order"})


class ScriptCompileError(Exception):
    """§3.3 taxonomy 4종을 하나로 감싼 컴파일 오류(400, 재시도 불가).

    `code`는 4종 중 하나, `line`/`col`은 1-기반 위치. `details`는 API 봉투의
    `ApiError.details`에 그대로 실리는 dict(`code`/`line`/`col`) — 최상위
    error_code는 기존 taxonomy(`VALIDATION_INVALID_FIELD`) 안에 머문다.
    """

    def __init__(self, code: str, message: str, line: int, col: int) -> None:
        if code not in SCRIPT_ERROR_CODES:
            raise ValueError(f"§3.3 taxonomy 밖 코드: {code!r}")
        super().__init__(f"[{code}] {message} (line {line}, col {col})")
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.details: dict[str, Any] = {"code": code, "line": line, "col": col}


@dataclass(frozen=True)
class CompiledScript:
    source: str
    ir: IRProgram
    ir_bytes: bytes
    estimate: ResourceEstimate
    registry_version: str
    script_hash: str
    grammar_version: str = GRAMMAR_VERSION
    ir_version: str = IR_VERSION


def compile_source(
    source: str,
    *,
    registry_version: str,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> CompiledScript:
    """소스 → `CompiledScript`. 실패는 `ScriptCompileError`(4종) 하나로 낸다."""
    try:
        tokens = tokenize(source)  # DSL-2
        program = parse(source)  # DSL-3
    except ScriptSyntaxError as exc:
        raise ScriptCompileError(exc.code, exc.message, exc.line, exc.col) from exc
    try:
        check_program(program)  # DSL-4
    except ScriptTypeError as exc:
        line, col = _locate_failing_decl(tokens, program, _type_fails)
        raise ScriptCompileError(exc.code, exc.message, line, col) from exc
    try:
        check_lookahead(tokens)  # DSL-5
    except ScriptLookaheadError as exc:
        raise ScriptCompileError(exc.code, exc.message, exc.line, exc.col) from exc
    try:
        estimate = check_resources(program, limits)  # DSL-6
    except ScriptResourceLimitError as exc:
        line, col = _locate_failing_decl(tokens, program, _resource_fails(limits))
        raise ScriptCompileError(exc.code, exc.message, line, col) from exc
    ir = lower_program(program)  # DSL-7 — ScriptLowerError는 감싸지 않는다(모듈 docstring)
    ir_bytes = to_bytes(ir)
    digest = script_hash(source=source, ir=ir, registry_version=registry_version)
    return CompiledScript(
        source=source,
        ir=ir,
        ir_bytes=ir_bytes,
        estimate=estimate,
        registry_version=registry_version,
        script_hash=digest,
    )


# ---- 위치 복원 ----


def _type_fails(prefix: Program) -> bool:
    try:
        check_program(prefix)
    except ScriptTypeError:
        return True
    return False


def _resource_fails(limits: ResourceLimits) -> Callable[[Program], bool]:
    def fails(prefix: Program) -> bool:
        try:
            check_resources(prefix, limits)
        except ScriptResourceLimitError:
            return True
        return False

    return fails


def decl_positions(tokens: list[Token]) -> list[tuple[int, int]]:
    """k번째 선언 시작 키워드 토큰의 (line, col) 목록. 선언 개수와 1:1이다."""
    return [
        (tok.line, tok.col)
        for tok in tokens
        if tok.kind is TokenKind.KEYWORD and tok.value in _DECL_KEYWORDS
    ]


def _locate_failing_decl(
    tokens: list[Token], program: Program, fails: Callable[[Program], bool]
) -> tuple[int, int]:
    """`fails(Program(decls[:k]))`가 참이 되는 최소 k(1-기반)를 이분탐색해 그
    선언의 위치를 돌려준다. 접두 단조성은 모듈 docstring 참조. 판정 함수가
    전체에서도 거짓이면(호출 계약 위반) 소스 시작 (1, 1)로 fail-closed."""
    positions = decl_positions(tokens)
    n = len(program.decls)
    if n == 0 or len(positions) != n:
        return (1, 1)
    lo, hi = 1, n
    while lo < hi:
        mid = (lo + hi) // 2
        if fails(Program(decls=program.decls[:mid])):
            hi = mid
        else:
            lo = mid + 1
    if not fails(Program(decls=program.decls[:lo])):
        return (1, 1)
    return positions[lo - 1]


__all__ = [
    "SCRIPT_ERROR_CODES",
    "CompiledScript",
    "ScriptCompileError",
    "compile_source",
    "decl_positions",
]
