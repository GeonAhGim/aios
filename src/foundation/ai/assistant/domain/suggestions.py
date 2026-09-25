"""U-3a -- ScriptCompileError -> user-facing fix suggestion mapping (pure).

Spec DoD: "on compile failure, the generated script returns the error and a
fix suggestion to the user" -- this file must know all four
`src.core.script.artifact.compile.SCRIPT_ERROR_CODES` values
(`SCRIPT_SYNTAX`/`SCRIPT_TYPE`/`SCRIPT_LOOKAHEAD`/`SCRIPT_RESOURCE_LIMIT`);
an unmapped code falls back to a generic message (not a silent failure, but
a quality regression -- update this dict whenever the taxonomy gains a new
code).
"""

from __future__ import annotations

_SUGGESTIONS: dict[str, str] = {
    "SCRIPT_SYNTAX": "구문 오류입니다. 괄호·연산자·들여쓰기 등 문법을 확인하세요: {message}",
    "SCRIPT_TYPE": "타입 오류입니다. 변수/지표의 타입이 선언과 일치하는지 확인하세요: {message}",
    "SCRIPT_LOOKAHEAD": (
        "미래 데이터를 참조하는 표현식이 있습니다(예: ta.security). "
        "참조 시점을 현재 바 이전으로 제한하세요: {message}"
    ),
    "SCRIPT_RESOURCE_LIMIT": (
        "스크립트가 리소스 상한(연산/plot 개수 등)을 초과했습니다. "
        "조건 수를 줄이거나 지표 계산을 단순화하세요: {message}"
    ),
}

_DEFAULT_SUGGESTION = "컴파일 오류입니다: {message}"


def suggest_fix(code: str, message: str) -> str:
    template = _SUGGESTIONS.get(code, _DEFAULT_SUGGESTION)
    return template.format(message=message)
