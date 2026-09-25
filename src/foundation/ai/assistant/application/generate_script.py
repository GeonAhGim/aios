"""U-3a -- natural language -> AIOS Script draft generation + compile check.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-3.

Flow: budget judgment (domain/budget) -> Agent Gateway scope proof
(`token_rules`, PROPOSE scope only -- ADR-2026-09-05-A) -> prompt-injection
detection/quarantine (domain/injection_guard) -> provider call ->
`compile_source` (the same function DSL-12 uses) compile judgment. **This
never executes or persists the compiled script** -- it only returns it (the
fact that no file here calls anything execution- or storage-related is
itself the UX-A2-equivalent safety contract).

The PROPOSE-scope proof uses a self-issued token that will never actually be
denied (no DB, does not go through AI-4's postgres repository -- this use
case needs no durable token). But the fact that `token_rules.authorize`
would fail hard if Scope.PAPER were ever requested is the evidence that
"this path can never structurally hold an order-execution scope" (an
adversarial test verifies this directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.core.script.artifact.compile import CompiledScript, ScriptCompileError, compile_source
from src.foundation.ai.assistant.domain import injection_guard
from src.foundation.ai.assistant.domain.budget import enforce_daily_budget
from src.foundation.ai.assistant.domain.injection_guard import InjectionFinding
from src.foundation.ai.assistant.domain.suggestions import suggest_fix
from src.foundation.ai.assistant.ports.script_provider import ScriptDraft, ScriptGenerationProvider
from src.foundation.ai.assistant.ports.usage_counter import UsageCounterStore
from src.foundation.ai.gateway.domain import token_rules
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope

_EPHEMERAL_TOKEN_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class GenerateScriptSuccess:
    status: str  # "compiled"
    draft: ScriptDraft
    compiled: CompiledScript
    elapsed_ms: int
    injection: InjectionFinding


@dataclass(frozen=True)
class GenerateScriptCompileFailure:
    status: str  # "compile_failed"
    draft: ScriptDraft
    error_code: str
    error_message: str
    line: int
    col: int
    suggestion: str
    injection: InjectionFinding


GenerateScriptResult = GenerateScriptSuccess | GenerateScriptCompileFailure


def _prove_propose_only_scope(*, tenant_id: UUID, now: datetime) -> None:
    """Pass `authorize` with a self-issued token that carries PROPOSE scope
    only -- this should fail if it ever requested Scope.PAPER/READ/RESEARCH
    (this path only ever proposes)."""
    token = AgentToken(
        token_id=uuid4(),
        tenant_id=tenant_id,
        scopes=frozenset({Scope.PROPOSE}),
        allow_instruments=frozenset(),
        notional_cap=Decimal(0),
        expires_at=now + _EPHEMERAL_TOKEN_TTL,
    )
    token_rules.authorize(token, Scope.PROPOSE, now)


async def generate_script(
    *,
    tenant_id: UUID,
    prompt: str,
    provider: ScriptGenerationProvider,
    registry_version: str,
    usage_store: UsageCounterStore,
    daily_cap: int,
    now: datetime | None = None,
) -> GenerateScriptResult:
    now = now or datetime.now(timezone.utc)

    used_today = await usage_store.get_count(tenant_id=tenant_id, day=now.date())
    enforce_daily_budget(used_today=used_today, daily_cap=daily_cap)

    _prove_propose_only_scope(tenant_id=tenant_id, now=now)

    finding = injection_guard.detect_injection(prompt)
    quarantined_prompt = injection_guard.quarantine_for_provider(prompt, finding)

    draft = await provider.generate_script(prompt=quarantined_prompt)

    await usage_store.increment(tenant_id=tenant_id, day=now.date())

    started = datetime.now(timezone.utc)
    try:
        compiled = compile_source(draft.source, registry_version=registry_version)
    except ScriptCompileError as exc:
        return GenerateScriptCompileFailure(
            status="compile_failed",
            draft=draft,
            error_code=exc.code,
            error_message=exc.message,
            line=exc.line,
            col=exc.col,
            suggestion=suggest_fix(exc.code, exc.message),
            injection=finding,
        )
    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return GenerateScriptSuccess(
        status="compiled",
        draft=draft,
        compiled=compiled,
        elapsed_ms=elapsed_ms,
        injection=finding,
    )
