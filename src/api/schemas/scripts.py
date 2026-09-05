"""DSL-12 — `POST /v1/scripts/compile` 요청·응답 스키마.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4 DSL-12.

응답은 아티팩트 신원(`script_hash`와 그 네 입력의 버전들)과 DSL-6 자원
산정치, IR 요약(sha256·명령 수)만 싣는다 — IR 본문은 싣지 않는다(DSL-13
편집기 미리보기는 해시·산정치·오류 위치만 쓰고, IR 본문이 필요해지면
필드 추가는 minor라 그때 얹는다). 오류 응답은 별도 스키마가 없다 — 전역
핸들러의 `ApiError` 봉투(`details.code/line/col`)가 계약이다.

`MAX_SOURCE_CHARS`는 렉서에 닿기 전 전송 계층 상한이다(자원 상한은 DSL-6이
AST 기준으로 따로 건다). 초과는 pydantic이 `VALIDATION_INVALID_FIELD`
(details.fields)로 거부한다.
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
