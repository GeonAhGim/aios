"""DSL-2 — AIOS Script 렉서.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.3(GRAMMAR_VERSION="aios-script-1" 문법), §9.4(DSL-2), §2.4 표(상한 260줄).

토큰화만 한다 — 파싱·AST 조립은 DSL-3(`grammar/parser.py`)의 몫이다(decision,
task-1235: ast.py를 임포트하지 않고 토큰 레벨에서 끝낸다). `tokenize()`는 소스
문자열 하나를 받아 `Token` 리스트를 반환하거나 `ScriptSyntaxError`를 던지는
순수 함수다 — I/O·전역 상태·난수·시계 없음.

미검증: §3.3 본문은 주석 마커를 명시하지 않는다. 이 저장소 전체가 Python이라
`#` 라인 주석을 관례로 채택했다 — DSL-3/파서 리프에서 다른 마커로 확정되면
이 파일만 고치면 된다(주석 스캔은 `_skip_trivia` 한 곳에 격리돼 있다).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

# §3.3 문법의 예약어. ta/math/series(네임스페이스)는 문법상 평범한 ident이고
# 파서가 "ns '.' ident" 형태로 해석하므로 여기서는 예약어로 다루지 않는다.
KEYWORDS = frozenset(
    {
        "input",
        "let",
        "plot",
        "signal",
        "order",
        "when",
        "and",
        "or",
        "not",
        "crosses_above",
        "crosses_below",
    }
)

# type := "int" | "float" | "bool" | "series<float>" | "series<bool>" 중
# 스칼라 원자 3개만 예약어다. "series<float>" 같은 복합 표기는 IDENT("series")
# LT IDENT("float") GT 네 토큰의 조합으로 남겨 파서가 조립한다(렉서는 문맥을
# 모른 채 문자만 본다 — "series < a" 같은 비교식과 동일한 토큰열).
TYPE_WORDS = frozenset({"int", "float", "bool"})

_TWO_CHAR_OPS = {"<=": "LE", "==": "EQEQ", ">=": "GE"}
_ONE_CHAR_OPS = {"<": "LT", ">": "GT", "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH"}
_DELIMS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ":": "COLON",
    "=": "ASSIGN",
    ".": "DOT",  # ns "." ident 호출 표기(call 규칙)에 필요 — §3.3 구분자 목록은
    # 요약이라 "."을 명시하지 않았지만 grammar 본문의 call 규칙이 요구한다.
}


class TokenKind(enum.Enum):
    KEYWORD = "KEYWORD"
    TYPE = "TYPE"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    OP = "OP"
    DELIM = "DELIM"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    """`value`는 원문 그대로의 렉심(lexeme) 문자열이다 — 숫자를 int/float로
    변환하거나 연산자 종류를 더 세분화하는 것은 파서/타입체커의 몫이다.
    `subtype`은 OP/DELIM일 때만 `_ONE_CHAR_OPS` 등의 이름표를 담아 파서가
    문자열 비교 없이 분기할 수 있게 한다(KEYWORD/TYPE/IDENT/NUMBER는 `value`
    자체가 이미 유일한 판별자라 subtype이 빈 문자열)."""

    kind: TokenKind
    value: str
    subtype: str
    line: int
    col: int


class ScriptSyntaxError(Exception):
    """§3.3 에러 taxonomy 4종 중 `SCRIPT_SYNTAX`(400, 재시도 불가) — 렉서
    단계에서 발생 가능한 유일한 코드다(TYPE/LOOKAHEAD/RESOURCE_LIMIT는 이후
    컴파일 단계 전용, decision: 이 리프에서 taxonomy를 늘리지 않는다)."""

    code = "SCRIPT_SYNTAX"

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(source: str) -> list[Token]:
    """AIOS Script 소스를 토큰 리스트로 변환한다. 마지막 토큰은 항상
    `TokenKind.EOF`(파서가 lookahead 시 특수 케이스 없이 끝을 알 수 있게)."""
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    n = len(source)

    def peek(offset: int = 0) -> str:
        idx = pos + offset
        return source[idx] if idx < n else ""

    while pos < n:
        ch = source[pos]

        if ch == "\r":
            if peek(1) == "\n":
                pos += 2
            else:
                pos += 1
            line += 1
            col = 1
            continue
        if ch == "\n":
            pos += 1
            line += 1
            col = 1
            continue
        if ch in (" ", "\t"):
            pos += 1
            col += 1
            continue
        if ch == "#":
            while pos < n and source[pos] not in ("\n", "\r"):
                pos += 1
                col += 1
            continue

        start_line, start_col = line, col

        if _is_ident_start(ch):
            begin = pos
            while pos < n and _is_ident_cont(source[pos]):
                pos += 1
                col += 1
            word = source[begin:pos]
            if word in KEYWORDS:
                tokens.append(Token(TokenKind.KEYWORD, word, "", start_line, start_col))
            elif word in TYPE_WORDS:
                tokens.append(Token(TokenKind.TYPE, word, "", start_line, start_col))
            else:
                tokens.append(Token(TokenKind.IDENT, word, "", start_line, start_col))
            continue

        if ch.isdigit():
            begin = pos
            while pos < n and source[pos].isdigit():
                pos += 1
                col += 1
            if peek() == ".":
                if peek(1).isdigit():
                    pos += 1
                    col += 1
                    while pos < n and source[pos].isdigit():
                        pos += 1
                        col += 1
                else:
                    raise ScriptSyntaxError(
                        "미종결 숫자 리터럴(소수점 뒤 숫자 없음)", line, col
                    )
            tokens.append(
                Token(TokenKind.NUMBER, source[begin:pos], "", start_line, start_col)
            )
            continue

        two = ch + peek(1)
        if two in _TWO_CHAR_OPS:
            tokens.append(Token(TokenKind.OP, two, _TWO_CHAR_OPS[two], start_line, start_col))
            pos += 2
            col += 2
            continue

        if ch in _ONE_CHAR_OPS:
            tokens.append(Token(TokenKind.OP, ch, _ONE_CHAR_OPS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        if ch in _DELIMS:
            tokens.append(Token(TokenKind.DELIM, ch, _DELIMS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        raise ScriptSyntaxError(f"예상치 못한 문자 {ch!r}", start_line, start_col)

    tokens.append(Token(TokenKind.EOF, "", "", line, col))
    return tokens
