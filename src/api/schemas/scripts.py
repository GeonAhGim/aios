"""DSL-12 — `POST /v1/scripts/compile` request/response schema.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4 DSL-12.

The response carries only artifact identity (`script_hash` plus the four
input versions), the DSL-6 resource estimate, and IR summary
(sha256 · instruction count) — it does not load the IR body (DSL-13
editor preview uses only hashes, estimates, and error locations; adding
fields is a minor change and can be done later if needed). Error
responses have no dedicated schema — the global handler's `ApiError`
envelope (`details.code/line/col`) is the contract.

`MAX_SOURCE_CHARS` is the transport-layer ceiling before it reaches the
lexer (the resource ceiling is enforced separately by DSL-6 on AST
counts). Over-limit payloads are rejected by pydantic as
`VALIDATION_INVALID_FIELD` (`details.fields`).
"""
from __future__ import annotations

import hashlib
from typing import Final

from pydantic import BaseModel, Field

from src.core.script.artifact.compile import CompiledScript

MAX_SOURCE_CHARS: Final = 64_000


class CompileScriptRequest(BaseModel):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class ResourceEstimateView(BaseModel):
    series_count: int
    lookback_total: int
    op_count: int
    call_count: int
    call_depth: int
    plot_count: int


class CompileScriptView(BaseModel):
    script_hash: str
    grammar_version: str
    ir_version: str
    registry_version: str
    ir_sha256: str
    instr_count: int
    resources: ResourceEstimateView
    elapsed_ms: int

    @classmethod
    def from_compiled(cls, compiled: CompiledScript, *, elapsed_ms: int) -> CompileScriptView:
        est = compiled.estimate
        return cls(
            script_hash=compiled.script_hash,
            grammar_version=compiled.grammar_version,
            ir_version=compiled.ir_version,
            registry_version=compiled.registry_version,
            ir_sha256=hashlib.sha256(compiled.ir_bytes).hexdigest(),
            instr_count=len(compiled.ir.instrs),
            resources=ResourceEstimateView(
                series_count=est.series_count,
                lookback_total=est.lookback_total,
                op_count=est.op_count,
                call_count=est.call_count,
                call_depth=est.call_depth,
                plot_count=est.plot_count,
            ),
            elapsed_ms=elapsed_ms,
        )


__all__ = [
    "MAX_SOURCE_CHARS",
    "CompileScriptRequest",
    "CompileScriptView",
    "ResourceEstimateView",
]
