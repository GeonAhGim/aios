"""GenerateProposal command -- AI-9.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-9
`application/generate_proposal.py` ("provider call -> schema validation ->
DSL compile (DSL-12) -> proposal save"), §3 error catalog
(`AI_PROPOSAL_SCHEMA` 400, `AI_PROPOSAL_COMPILE` 400 with DSL error location,
`AI_PROVIDER_UNAVAILABLE` 503), §5 "proposal submission: idempotent on
(token_id, script_hash)".

This is the single caller of AI-8's `domain/proposal_rules.py::evaluate_proposal_candidate`
(schema -> compile -> data-scope coverage -> forbidden API, exactly that
order) plus the provider call that produces the candidate payload in the
first place (AI-6/7/7b adapters, or any external agent channel -- this
module depends only on the `ModelProvider` Protocol, AI-5, never a concrete
adapter). `evaluate_proposal_candidate`'s own docstring names this module as
the one that builds the accepted `StrategyProposal` and performs the
`AI_PROPOSAL_SCHEMA`/`AI_PROPOSAL_COMPILE` wire mapping
(`domain/proposal_rules.py::ProposalRuleError` docstring).

`ProposalRepository` is a `Protocol` this module defines and depends on --
no concrete adapter ships in this leaf, consistent with §2.3's module table
listing only contracts/domain/application for Strategy Factory (no
`adapters/` row). A postgres implementation is deferred to whichever future
leaf wires `generate_proposal` into the MCP `tools_propose.py` pipeline
(AI-16). Tenant/token budget reservation (`domain/budget.py`, AI-5) likewise
happens before this function is ever called (the same "before generate() is
ever invoked" contract `GenerationBudget`'s own docstring states) -- this
module accepts an already-sized `GenerationBudget` rather than reserving
against a `TenantBudget` itself, keeping this leaf's dependency edges
exactly AI-6+AI-8 (the spec's own §9 row), not AI-5's budget wiring too.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from src.foundation.ai.factory.contracts.v1 import ProviderRef, StrategyProposal
from src.foundation.ai.factory.domain.proposal_rules import (
    ProposalCompileRejectedError,
    ProposalDataScopeUncoveredError,
    ProposalForbiddenApiError,
    ProposalRuleError,
    ProposalSchemaRejectedError,
    evaluate_proposal_candidate,
)
from src.foundation.ai.factory.domain.schema import proposal_draft_json_schema
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, ModelProvider
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan

__all__ = [
    "GenerateProposalError",
    "ProposalRejected",
    "ProposalCompileRejected",
    "ProviderUnavailable",
    "ProposalRepository",
    "generate_proposal",
]


@runtime_checkable
class ProposalRepository(Protocol):
    """Storage port `generate_proposal` depends on -- see module docstring
    for why no concrete adapter ships in this leaf."""

    async def find_by_idempotency_key(
        self, *, created_by_token: UUID, script_hash: str
    ) -> StrategyProposal | None: ...

    async def save(self, proposal: StrategyProposal) -> None: ...


class GenerateProposalError(Exception):
    """Base for this module's wire-shaped errors. `code`/`http_status` are
    spec §3's error catalog verbatim -- a future router leaf (AI-17) reads
    these two fields directly instead of re-deriving them from exception
    type."""

    def __init__(self, *, code: str, http_status: int, message: str) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(message)


class ProposalRejected(GenerateProposalError):
    """§3 `AI_PROPOSAL_SCHEMA` (400) -- covers all three non-compile
    `proposal_rules` checks (schema/data-scope/forbidden-API). The spec's
    error catalog names only two proposal-specific codes
    (`AI_PROPOSAL_SCHEMA`, `AI_PROPOSAL_COMPILE`); data-scope and
    forbidden-API are both proposal-content rejections indistinguishable
    from a schema violation at the wire level, so they share
    `AI_PROPOSAL_SCHEMA` here -- `reason` still carries the specific
    `ProposalRejectionReason` value (as a string) for logging/debugging."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(code="AI_PROPOSAL_SCHEMA", http_status=400, message=detail)


class ProposalCompileRejected(GenerateProposalError):
    """§3 `AI_PROPOSAL_COMPILE` (400, "DSL error location included") -- the
    only wire error that must carry `line`/`col`, taken from DSL-12's own
    `ScriptCompileError` unchanged (via `ProposalCompileRejectedError`)."""

    def __init__(self, cause: ProposalCompileRejectedError) -> None:
        self.dsl_code = cause.code
        self.line = cause.line
        self.col = cause.col
        super().__init__(code="AI_PROPOSAL_COMPILE", http_status=400, message=str(cause))


class ProviderUnavailable(GenerateProposalError):
    """§3 `AI_PROVIDER_UNAVAILABLE` (503) -- the provider call itself raised
    (network failure, upstream error status, structured-output refusal).
    Wraps whatever the concrete adapter raised (its own exception
    hierarchy, e.g. `AnthropicProviderError`) without inspecting
    adapter-specific attributes -- this module stays provider-neutral (§1)."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(
            code="AI_PROVIDER_UNAVAILABLE",
            http_status=503,
            message=f"provider call failed: {cause}",
        )


async def generate_proposal(
    *,
    provider: ModelProvider,
    provider_ref: ProviderRef,
    prompt: PromptTemplate,
    budget: GenerationBudget,
    coverage_spans: Sequence[CoverageSpan],
    registry_version: str,
    repository: ProposalRepository,
    created_by_token: UUID,
    proposal_id_factory: Callable[[], UUID] = uuid4,
) -> StrategyProposal:
    """§2.3 AI-9 row verbatim pipeline: provider -> schema -> compile ->
    save, in that order. Idempotent on `(created_by_token, script_hash)`
    (spec §5) -- `script_hash` is only known after compile succeeds, so the
    idempotency lookup happens between compile and save, not before the
    provider call."""
    try:
        output = await provider.generate(proposal_draft_json_schema(), prompt, budget)
    except Exception as exc:  # noqa: BLE001 -- provider-neutral, see ProviderUnavailable docstring
        raise ProviderUnavailable(exc) from exc

    try:
        draft, compiled = evaluate_proposal_candidate(
            payload=output.data,
            coverage_spans=coverage_spans,
            registry_version=registry_version,
        )
    except ProposalCompileRejectedError as exc:
        raise ProposalCompileRejected(exc) from exc
    except ProposalSchemaRejectedError as exc:
        raise ProposalRejected("schema_invalid", str(exc)) from exc
    except ProposalDataScopeUncoveredError as exc:
        raise ProposalRejected("data_scope_uncovered", str(exc)) from exc
    except ProposalForbiddenApiError as exc:
        raise ProposalRejected("forbidden_api", str(exc)) from exc
    except ProposalRuleError as exc:  # pragma: no cover -- fail-closed catch-all
        raise ProposalRejected("unknown", str(exc)) from exc

    existing = await repository.find_by_idempotency_key(
        created_by_token=created_by_token, script_hash=compiled.script_hash
    )
    if existing is not None:
        return existing

    proposal = StrategyProposal(
        proposal_id=proposal_id_factory(),
        script_source=draft.script_source,
        hypothesis=draft.hypothesis,
        data_scope=draft.data_scope,
        params=draft.params,
        provider_ref=provider_ref,
        prompt_hash=output.prompt_hash,
        created_by_token=created_by_token,
    )
    await repository.save(proposal)
    return proposal
