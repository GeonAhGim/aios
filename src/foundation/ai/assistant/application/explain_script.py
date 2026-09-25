"""U-3a -- natural-language explanation of an existing AIOS Script source.

First confirms the source compiles via `compile_source` -- explaining a
script that does not compile risks inventing logic that is not actually
there (same principle as UX-A5: never narrate without grounding), so on
compile failure this returns the compile error as-is instead of generating
an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.script.artifact.compile import ScriptCompileError, compile_source
from src.foundation.ai.assistant.domain.suggestions import suggest_fix
from src.foundation.ai.assistant.ports.script_provider import ScriptGenerationProvider


@dataclass(frozen=True)
class ExplainScriptSuccess:
    status: str  # "explained"
    explanation: str
    script_hash: str


@dataclass(frozen=True)
class ExplainScriptCompileFailure:
    status: str  # "compile_failed"
    error_code: str
    error_message: str
    line: int
    col: int
    suggestion: str


ExplainScriptResult = ExplainScriptSuccess | ExplainScriptCompileFailure


async def explain_script(
    *, source: str, provider: ScriptGenerationProvider, registry_version: str
) -> ExplainScriptResult:
    try:
        compiled = compile_source(source, registry_version=registry_version)
    except ScriptCompileError as exc:
        return ExplainScriptCompileFailure(
            status="compile_failed",
            error_code=exc.code,
            error_message=exc.message,
            line=exc.line,
            col=exc.col,
            suggestion=suggest_fix(exc.code, exc.message),
        )
    explanation = await provider.explain_script(source=source)
    return ExplainScriptSuccess(
        status="explained", explanation=explanation, script_hash=compiled.script_hash
    )
