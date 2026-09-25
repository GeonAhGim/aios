"""DSL-12 — `POST /v1/scripts/compile`: AIOS Script compilation (hash, checksum, error location).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4 DSL-12,
§3.3 (error taxonomy 4 types, location info).

Rule §6 of 71: The router only injects auth/TenantContext, validates transport, and
calls `compile_source` (src/core/script/artifact/compile.py). It does not persist
anything (artifact persistence is MP/DSL follow-up, task-1535 decision) — it returns
only the pure compilation result wrapped in the `ok()` envelope. Domain exceptions
(`ScriptCompileError`) are not caught directly — `exception_registry.py` (EXCEPTION_MAP)
translates them to `VALIDATION_INVALID_FIELD` (400) and loads
`details.code/line/col` into the envelope (zero raw HTTPException calls,
PLT-21 guard target).

Authentication: `get_tenant_context` (PLT-28) — gateway auth + tenant context.
Compilation does not read tenant data, but receives the context to emit common
log fields (tenant_id, script_hash) per §5. Registry version uses IND-1
`DEFAULT_REGISTRY.registry_hash()` (spec canonical hash) as-is.

Performance (DoD "compile ≤300ms"): `elapsed_ms` is included in the response so
clients and tests can measure it empirically — this router does not assert latency.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.scripts import CompileScriptRequest, CompileScriptView
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.script.artifact.compile import compile_source
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/scripts", tags=["scripts"])


def get_indicator_registry() -> IndicatorRegistry:
    """IND-1 default registry (single process instance). Dependency that tests can override."""
    return DEFAULT_REGISTRY


@router.post("/compile", response_model=ApiResponse[CompileScriptView])
async def compile_script(
    body: CompileScriptRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    registry: IndicatorRegistry = Depends(get_indicator_registry),
) -> ApiResponse[CompileScriptView]:
    started = time.perf_counter()
    compiled = compile_source(body.source, registry_version=registry.registry_hash())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "script.compile",
        extra={
            "event_type": "script.compile",
            "tenant_id": str(tenant.tenant_id),
            "script_hash": compiled.script_hash,
            "duration_ms": elapsed_ms,
        },
    )
    return ok(CompileScriptView.from_compiled(compiled, elapsed_ms=elapsed_ms))


__all__ = ["get_indicator_registry", "router"]
