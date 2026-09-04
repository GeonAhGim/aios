"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-5 —
AIOS Script 미래참조(lookahead)·repaint 패턴 정적 검출기.

Spec: §2 표 85행(`analysis/lookahead.py`, 240줄 상한), §3 168행("컴파일 시
lookahead.py가 시리즈 오프셋 부호와 security()류 부재를 정적 검증(위반 =
SCRIPT_LOOKAHEAD)"), §9.4 DoD("음수 인덱스·변수 인덱스·미래 함수 거부").

DSL-2(`grammar/lexer.py`) `tokenize()`가 만든 토큰열만 입력으로 받는다 —
DSL-1 `Program` AST(`grammar/ast.py`)는 참조하지 않는다. 두 가지 이유:

1. 위치. §3.3 에러 taxonomy는 `SCRIPT_LOOKAHEAD` 위반에도 (line, col)을
   요구하는데 `ScriptNode`엔 위치 필드가 없다(DSL-4 `typing/checker.py`가
   같은 이유로 위치를 못 낸다고 이미 명시한 제약이자 decision — 이번
   사이클에 다른 worker 소유인 AST를 임의로 확장하지 않는다). 토큰은
   DSL-2가 이미 (line, col)을 갖고 있으므로 이 리프가 그 값을 그대로
   재사용하면 "DSL-2 토큰 위치와 일치"가 설계상 자명해진다.
2. 타입 무관성. `postfix := primary "[" INT "]"`의 INT는 문법상 이미
   "0 이상 정수 상수"로 고정돼 있고(DSL-1 `PostfixExpr.index: int | None
   = Field(ge=0)`), 이를 벗어나는 모든 형태("[-1]", "[i]", "[n+1]" 등)는
   base의 타입(스칼라/시리즈)과 무관하게 거부 대상이다 — DSL-4의 TypeEnv를
   가져와도 이 판정은 달라지지 않는다. 그래서 DSL-4 타입 정보를 소비하지
   않는다(재구현할 타입 추론 자체가 애초에 필요 없다).

Fail-closed: "[" 다음이 정확히 `NUMBER "]"` 모양이 아니면 그 이유를 더
따지지 않고 전부 거부한다 — decision: "판정 불가를 통과로 처리하는 순간 이
리프의 존재 이유가 사라진다". security()류 호출은 §3.3 문법이 애초에
`ns "." ident "(" args ")"` 형태만 허용하므로 ns/ident 어느 자리에 오든
(`security(...)`, `ta.security(...)`, `security.request(...)`) 동일하게
잡아낸다.

순수 함수 — 스크립트 실행·I/O·DB 없음(DoD (4)).

미검증: `_FUTURE_FUNCTION_NAMES`의 정확한 함수명 목록은 특정 거래소·벤더
문서가 아니라 스펙 문구("security()류")에서 추정했다 — TradingView Pine
Script의 `security()`/`request.security()`(다른 심볼·타임프레임의 아직
확정되지 않은 봉을 끌어와 repaint를 유발하는 대표적 함수)를 참조했을 뿐,
AIOS Script 자체 레지스트리(DSL-9, 아직 없음)가 확정한 목록이 아니다.
"""
from __future__ import annotations

from collections.abc import Sequence

from src.core.script.grammar.lexer import Token, TokenKind, tokenize

_FUTURE_FUNCTION_NAMES = frozenset({"security", "request_security"})


class ScriptLookaheadError(Exception):
    """§3.3 에러 taxonomy의 `SCRIPT_LOOKAHEAD`(400, 재시도 불가)."""

    code = "SCRIPT_LOOKAHEAD"

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def check_source(source: str) -> None:
    """AIOS Script 소스를 토큰화한 뒤 `check_lookahead`를 적용한다."""
    check_lookahead(tokenize(source))


def check_lookahead(tokens: Sequence[Token]) -> None:
    """미래참조·repaint 패턴을 정적 검출한다. 위반 시 `ScriptLookaheadError`.

    §3.3 문법 전체에서 미래참조가 발생 가능한 두 지점만 스캔한다 — 그 외
    프로덕션은 반복·재귀·외부 I/O가 없어 결정론이 구조적으로 보장된다
    (DSL-1 decision 참조):

    - postfix index: "[" 다음 토큰이 `NUMBER "]"` 모양이 아니면 거부.
    - call: `ns "." ident` 어느 한쪽이 `_FUTURE_FUNCTION_NAMES`면 거부.
    """
    for i, tok in enumerate(tokens):
        if tok.kind is TokenKind.DELIM and tok.value == "[":
            _check_index(tokens, i)
        elif tok.kind is TokenKind.IDENT and tok.value in _FUTURE_FUNCTION_NAMES:
            _check_future_call(tokens, i)


def _at(tokens: Sequence[Token], idx: int) -> Token:
    return tokens[idx] if idx < len(tokens) else tokens[-1]  # tokens[-1] == EOF


def _check_index(tokens: Sequence[Token], bracket_idx: int) -> None:
    inner = _at(tokens, bracket_idx + 1)
    closing = _at(tokens, bracket_idx + 2)
    is_plain_constant = (
        inner.kind is TokenKind.NUMBER
        and "." not in inner.value
        and closing.kind is TokenKind.DELIM
        and closing.value == "]"
    )
    if is_plain_constant:
        return
    if inner.kind is TokenKind.OP and inner.value == "-":
        raise ScriptLookaheadError(
            "시리즈 오프셋에 음수 인덱스(미래 참조)는 금지합니다", inner.line, inner.col
        )
    if inner.kind is TokenKind.IDENT:
        raise ScriptLookaheadError(
            "시리즈 오프셋은 상수만 허용합니다"
            "(변수 인덱스는 정적으로 미래 참조가 아님을 증명할 수 없어 거부)",
            inner.line,
            inner.col,
        )
    raise ScriptLookaheadError(
        "시리즈 오프셋이 0 이상 정수 상수 하나가 아닙니다"
        "(정적으로 안전함을 증명할 수 없어 fail-closed 거부)",
        inner.line,
        inner.col,
    )


def _check_future_call(tokens: Sequence[Token], ident_idx: int) -> None:
    nxt = _at(tokens, ident_idx + 1)
    if nxt.kind is TokenKind.DELIM and nxt.value in ("(", "."):
        tok = tokens[ident_idx]
        raise ScriptLookaheadError(
            f"미래 데이터 접근 함수 {tok.value!r} 호출은 금지합니다(security()류)",
            tok.line,
            tok.col,
        )
