"""U-3a -- `POST /v1/assistant/{generate-script,explain-script,explain-backtest}`.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-3,
ADR-2026-09-09-B Decision C, ADR-2026-09-05-A(Agent Gateway).

Rule 71 §6: the router only wires auth/TenantContext, does transport
validation, calls the application layer, and converts to a view. There is no
execution/persistence anywhere in this file (zero order-path imports --
statically verified by
tests/adversarial/assistant/test_no_execution_access.py).

When the `FF_U3_AI_ASSISTANT` feature flag (off by default) is off, all
three endpoints respond with `AssistantFeatureDisabledError` (404) -- the §U
common DoD "fully inactive when the flag is off". Domain exceptions are not
caught here -- `exception_registry_foundation_ai_assistant.py` translates
them via EXCEPTION_MAP (zero raw HTTPException).
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.assistant import (
    CompileErrorView,
    ExplainBacktestRequest,
    ExplainBacktestView,
    ExplainScriptRequest,
    ExplainScriptView,
    GenerateScriptRequest,
    GenerateScriptView,
)
from src.api.schemas.backtests import QuickBacktestResultView
from src.api.schemas.scripts import CompileScriptView
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.foundation.ai.assistant.adapters.anthropic_provider import get_default_provider
from src.foundation.ai.assistant.adapters.in_memory_usage_counter import (
    get_process_usage_counter_store,
)
from src.foundation.ai.assistant.application.errors import AssistantFeatureDisabledError
from src.foundation.ai.assistant.application.explain_backtest import explain_backtest
from src.foundation.ai.assistant.application.explain_script import (
    ExplainScriptCompileFailure,
    explain_script,
)
from src.foundation.ai.assistant.application.generate_script import (
    GenerateScriptCompileFailure,
    generate_script,
)
from src.foundation.ai.assistant.ports.script_provider import ScriptGenerationProvider
from src.foundation.ai.assistant.ports.usage_counter import UsageCounterStore
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])

FEATURE_FLAG_NAME = "FF_U3_AI_ASSISTANT"
DAILY_CAP_ENV_NAME = "AI_ASSISTANT_DAILY_CAP"
DEFAULT_DAILY_CAP = 50


def flag_enabled(name: str) -> bool:
    """Off by default (staged rollout ahead of production exposure) --
    `background_loops.flag_enabled` (on by default, a background-loop
    toggle) has the opposite default direction, so this router defines its
    own copy (reusing that one would flip the meaning)."""
    return os.environ.get(name, "0") == "1"


def _daily_cap() -> int:
    raw = os.environ.get(DAILY_CAP_ENV_NAME, "")
    return int(raw) if raw else DEFAULT_DAILY_CAP


def require_flag() -> None:
    """Declared as a FastAPI dependency so it is evaluated before other
    dependencies such as `Depends(get_script_provider)` -- calling this from
    the function body would let another Depends resolve first (e.g. a 503
    for an unconfigured provider), producing the wrong error even when the
    flag is off."""
    if not flag_enabled(FEATURE_FLAG_NAME):
        raise AssistantFeatureDisabledError(f"{FEATURE_FLAG_NAME} is off")


def get_indicator_registry() -> IndicatorRegistry:
    """Same process-wide single instance as DSL-12 (`scripts.py`) and BT-10c
    (`backtests.py`) -- a dependency tests can override."""
    return DEFAULT_REGISTRY


def get_script_provider() -> ScriptGenerationProvider:
    """The default Claude adapter. Raises `ProviderNotConfiguredError` (503)
    when `ANTHROPIC_API_KEY` is blank -- tests override this dependency with
    a fake provider."""
    return get_default_provider()


def get_usage_counter_store() -> UsageCounterStore:
    return get_process_usage_counter_store()


def _error_view(
    exc: GenerateScriptCompileFailure | ExplainScriptCompileFailure,
) -> CompileErrorView:
    return CompileErrorView(
        code=exc.error_code,
        message=exc.error_message,
        line=exc.line,
        col=exc.col,
        suggestion=exc.suggestion,
    )


@router.post("/generate-script", response_model=ApiResponse[GenerateScriptView])
async def generate_script_endpoint(
    body: GenerateScriptRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    _flag: None = Depends(require_flag),
    provider: ScriptGenerationProvider = Depends(get_script_provider),
    registry: IndicatorRegistry = Depends(get_indicator_registry),
    usage_store: UsageCounterStore = Depends(get_usage_counter_store),
) -> ApiResponse[GenerateScriptView]:
    result = await generate_script(
        tenant_id=tenant.tenant_id,
        prompt=body.prompt,
        provider=provider,
        registry_version=registry.registry_hash(),
        usage_store=usage_store,
        daily_cap=_daily_cap(),
    )
    logger.info(
        "assistant.generate_script",
        extra={
            "event_type": "assistant.generate_script",
            "tenant_id": str(tenant.tenant_id),
            "status": result.status,
            "injection_detected": result.injection.detected,
        },
    )
    if isinstance(result, GenerateScriptCompileFailure):
        view = GenerateScriptView(
            status=result.status,
            source=result.draft.source,
            script=None,
            error=_error_view(result),
            injected_instruction_ignored=result.injection.detected,
            ignored_snippets=list(result.injection.matched_snippets),
        )
        return ok(view)
    view = GenerateScriptView(
        status=result.status,
        source=result.draft.source,
        script=CompileScriptView.from_compiled(result.compiled, elapsed_ms=result.elapsed_ms),
        error=None,
        injected_instruction_ignored=result.injection.detected,
        ignored_snippets=list(result.injection.matched_snippets),
    )
    return ok(view)


@router.post("/explain-script", response_model=ApiResponse[ExplainScriptView])
async def explain_script_endpoint(
    body: ExplainScriptRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    _flag: None = Depends(require_flag),
    provider: ScriptGenerationProvider = Depends(get_script_provider),
    registry: IndicatorRegistry = Depends(get_indicator_registry),
) -> ApiResponse[ExplainScriptView]:
    result = await explain_script(
        source=body.source, provider=provider, registry_version=registry.registry_hash()
    )
    logger.info(
        "assistant.explain_script",
        extra={
            "event_type": "assistant.explain_script",
            "tenant_id": str(tenant.tenant_id),
            "status": result.status,
        },
    )
    if isinstance(result, ExplainScriptCompileFailure):
        return ok(
            ExplainScriptView(
                status=result.status, explanation=None, script_hash=None, error=_error_view(result)
            )
        )
    return ok(
        ExplainScriptView(
            status=result.status,
            explanation=result.explanation,
            script_hash=result.script_hash,
            error=None,
        )
    )


def _backtest_metrics(result: QuickBacktestResultView) -> dict[str, str]:
    """Serialize Decimal fields to strings -- only text goes to the
    provider (no recomputation, the already-computed values as-is)."""

    def _dec(value: Decimal) -> str:
        return str(value)

    return {
        "final_equity": _dec(result.final_equity),
        "cash": _dec(result.cash),
        "position_quantity": _dec(result.position_quantity),
        "funding_cost": _dec(result.funding_cost),
        "borrow_cost": _dec(result.borrow_cost),
        "bars": str(result.bars),
        "fills_count": str(len(result.fills)),
        "expired_orders": str(result.expired_orders),
        "warnings_count": str(len(result.warnings)),
    }


@router.post("/explain-backtest", response_model=ApiResponse[ExplainBacktestView])
async def explain_backtest_endpoint(
    body: ExplainBacktestRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    _flag: None = Depends(require_flag),
    provider: ScriptGenerationProvider = Depends(get_script_provider),
) -> ApiResponse[ExplainBacktestView]:
    result = await explain_backtest(
        metrics=_backtest_metrics(body.result), provider=provider, question=body.question
    )
    logger.info(
        "assistant.explain_backtest",
        extra={
            "event_type": "assistant.explain_backtest",
            "tenant_id": str(tenant.tenant_id),
            "injection_detected": result.injection.detected,
        },
    )
    return ok(
        ExplainBacktestView(
            narrative=result.narrative,
            injected_instruction_ignored=result.injection.detected,
            ignored_snippets=list(result.injection.matched_snippets),
        )
    )


__all__ = [
    "get_indicator_registry",
    "get_script_provider",
    "get_usage_counter_store",
    "flag_enabled",
    "router",
]
