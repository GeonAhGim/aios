"""U-3a -- request/response schemas for
`POST /v1/assistant/{generate-script,explain-script,explain-backtest}`.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-3.

A compile failure is not an HTTP error -- it is a `status="compile_failed"`
branch inside a 200 success response (a deliberate difference from DSL-12's
`/v1/scripts/compile`): this endpoint does not "expose the compiler
directly", it is a conversational assistant response that explains the
error and a fix suggestion together, so it is a domain result, not a
transport-layer error.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.api.schemas.backtests import QuickBacktestResultView
from src.api.schemas.scripts import MAX_SOURCE_CHARS, CompileScriptView

MAX_PROMPT_CHARS = 4_000
MAX_QUESTION_CHARS = 2_000


class CompileErrorView(BaseModel):
    code: str
    message: str
    line: int
    col: int
    suggestion: str


class GenerateScriptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


class GenerateScriptView(BaseModel):
    status: str  # "compiled" | "compile_failed"
    source: str
    script: CompileScriptView | None = None
    error: CompileErrorView | None = None
    injected_instruction_ignored: bool
    ignored_snippets: list[str]


class ExplainScriptRequest(BaseModel):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class ExplainScriptView(BaseModel):
    status: str  # "explained" | "compile_failed"
    explanation: str | None = None
    script_hash: str | None = None
    error: CompileErrorView | None = None


class ExplainBacktestRequest(BaseModel):
    result: QuickBacktestResultView
    question: str | None = Field(default=None, max_length=MAX_QUESTION_CHARS)


class ExplainBacktestView(BaseModel):
    narrative: str
    injected_instruction_ignored: bool
    ignored_snippets: list[str]


__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_QUESTION_CHARS",
    "CompileErrorView",
    "GenerateScriptRequest",
    "GenerateScriptView",
    "ExplainScriptRequest",
    "ExplainScriptView",
    "ExplainBacktestRequest",
    "ExplainBacktestView",
]
