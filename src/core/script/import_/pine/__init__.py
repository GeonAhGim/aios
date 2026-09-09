"""DSL-14 — Pine Script v5 부분 문법 임포터(렉서·파서).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.9(DSL-14), 선행 DSL-3(`grammar/parser.py`, task-1337, 5812ee4).

디렉터리명이 `import`면 파이썬 예약어라 패키지로 쓸 수 없어 `import_`를
쓴다(명세의 `script/import/pine/`에 대응). 여기서 만드는 AST/렉서는 기존
AIOS Script 문법(`src/core/script/grammar/{lexer,parser}.py`)과 다른
언어(Pine v5)를 다루므로 그 모듈들을 재사용하지도, 수정하지도 않는다.

AIOS Script로의 변환(transpile)은 DSL-15(`transpile.py`)의 몫이다 —
`parse()`가 반환하는 `PineProgram`이 DSL-14의 최종 산출물이자 DSL-15의
입력이다.
"""
from __future__ import annotations

from src.core.script.import_.pine.ast import PineProgram
from src.core.script.import_.pine.lexer import PineSyntaxError, Token, TokenKind, tokenize
from src.core.script.import_.pine.parser import parse
from src.core.script.import_.pine.transpile import (
    PineTranspileError,
    TranspileResult,
    transpile_and_verify,
    transpile_program,
    transpile_source,
)

__all__ = [
    "PineProgram",
    "PineSyntaxError",
    "PineTranspileError",
    "Token",
    "TokenKind",
    "TranspileResult",
    "parse",
    "tokenize",
    "transpile_and_verify",
    "transpile_program",
    "transpile_source",
]
