"""DSL-14 — Pine Script v5 부분 문법 렉서.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
DSL-14("script/import/pine/{lexer,parser}.py — Pine Script v5 부분 문법").

토큰화만 한다 — 파싱·거부 판단은 `parser.py`의 몫이다. `tokenize()`는
소스 문자열 하나를 받아 `Token` 리스트를 반환하거나 `PineSyntaxError`를
던지는 순수 함수다 — I/O·전역 상태·난수·시계 없음.

Pine은 세미콜론이 없고 줄바꿈이 문장 경계다(AIOS Script와의 핵심 차이).
괄호/대괄호 depth를 세어 depth>0인 동안의 줄바꿈은 공백처럼 건너뛰고
(다중 줄 호출 인자 표기를 허용), depth==0에서만 `NEWLINE` 토큰을 낸다 —
Pine의 들여쓰기 기반 블록(if/for)은 이 리프의 지원 범위 밖(파서가 키워드
자체를 거부)이라 들여쓰기 규칙까지 구현하지 않는다.

미검증: 이 파서는 TradingView 비공개 문법 명세가 아니라 공개된 Pine v5
언어 레퍼런스의 일반적으로 알려진 어휘 규칙(식별자·숫자·문자열·연산자)만
따른다 — 정확한 원본 문법 EBNF와 100% 일치를 보증하지 않는다.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

# 코드가 시작·종료 지점을 명시적으로 검사해야 하는 예약어만 KEYWORD로 둔다.
# ta/input/strategy(네임스페이스)는 문법상 평범한 ident이고 파서가
# "ns '.' ident" 형태로 해석하므로 여기서는 예약어로 다루지 않는다.
KEYWORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "true",
        "false",
        "var",
        "varip",
        "if",
        "for",
        "while",
        "switch",
    }
)

_TWO_CHAR_OPS = {
    "<=": "LE",
    ">=": "GE",
    "==": "EQEQ",
    "!=": "NEQ",
    ":=": "REASSIGN",
    "=>": "ARROW",
}
_ONE_CHAR_OPS = {
    "<": "LT",
    ">": "GT",
    "+": "PLUS",
    "-": "MINUS",
    "*": "STAR",
    "/": "SLASH",
    "%": "PERCENT",
    "=": "ASSIGN",
}
_DELIMS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ".": "DOT",
}


class TokenKind(enum.Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OP"
    DELIM = "DELIM"
    NEWLINE = "NEWLINE"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    """`value`는 원문 그대로의 렉심(lexeme)이다 — STRING의 `value`는 여는·닫는
    따옴표를 뗀 내용(이스케이프 해제 완료)이고, NUMBER는 변환 전 원문 그대로다
    (int/float 승격은 파서 몫). `subtype`은 OP/DELIM일 때만 채워 파서가 문자열
    비교 없이 분기하게 한다."""

    kind: TokenKind
    value: str
    subtype: str
    line: int
    col: int


class PineSyntaxError(Exception):
    """Pine 소스를 토큰화·파싱할 수 없을 때 던진다 — 항상 (line, col)을
    담아 호출자가 오류 위치를 표시할 수 있게 한다. 문법표 밖 구문(미지원)과
    진짜 어휘/구문 오류를 이 리프에서는 taxonomy로 나누지 않는다(둘 다
    "이 Pine 코드는 임포트할 수 없다"는 동일한 결론이라 호출자 관점에서는
    구분할 필요가 없다 — decision, AIOS Script DSL-3의 `ScriptSyntaxError`
    단일 코드 재사용 선례를 따른다)."""

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
    """Pine 소스를 토큰 리스트로 변환한다. 마지막 토큰은 항상
    `TokenKind.EOF`(파서가 lookahead 시 특수 케이스 없이 끝을 알 수 있게)."""
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    n = len(source)
    depth = 0  # ()/[] 중첩 깊이 — 0보다 크면 줄바꿈을 공백처럼 무시한다.

    def peek(offset: int = 0) -> str:
        idx = pos + offset
        return source[idx] if idx < n else ""

    while pos < n:
        ch = source[pos]

        if ch == "\r" or ch == "\n":
            advance = 2 if ch == "\r" and peek(1) == "\n" else 1
            pos += advance
            line += 1
            col = 1
            if depth == 0 and tokens and tokens[-1].kind is not TokenKind.NEWLINE:
                tokens.append(Token(TokenKind.NEWLINE, "\n", "", line - 1, col))
            continue
        if ch in (" ", "\t"):
            pos += 1
            col += 1
            continue
        if ch == "/" and peek(1) == "/":
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
            kind = TokenKind.KEYWORD if word in KEYWORDS else TokenKind.IDENT
            tokens.append(Token(kind, word, "", start_line, start_col))
            continue

        if ch.isdigit():
            begin = pos
            while pos < n and source[pos].isdigit():
                pos += 1
                col += 1
            if peek() == "." and peek(1).isdigit():
                pos += 1
                col += 1
                while pos < n and source[pos].isdigit():
                    pos += 1
                    col += 1
            tokens.append(Token(TokenKind.NUMBER, source[begin:pos], "", start_line, start_col))
            continue

        if ch in ("'", '"'):
            quote = ch
            pos += 1
            col += 1
            chars: list[str] = []
            while True:
                if pos >= n or source[pos] in ("\n", "\r"):
                    raise PineSyntaxError("미종결 문자열 리터럴", start_line, start_col)
                cur = source[pos]
                if cur == quote:
                    pos += 1
                    col += 1
                    break
                if cur == "\\" and peek(1) in (quote, "\\"):
                    chars.append(peek(1))
                    pos += 2
                    col += 2
                    continue
                chars.append(cur)
                pos += 1
                col += 1
            tokens.append(
                Token(TokenKind.STRING, "".join(chars), "", start_line, start_col)
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
            if ch in ("(", "["):
                depth += 1
            elif ch in (")", "]"):
                depth = max(0, depth - 1)
            tokens.append(Token(TokenKind.DELIM, ch, _DELIMS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        raise PineSyntaxError(f"예상치 못한 문자 {ch!r}", start_line, start_col)

    tokens.append(Token(TokenKind.EOF, "", "", line, col))
    return tokens
