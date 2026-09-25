"""Model provider port -- Protocol + wire-shaped value objects, no I/O.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-5
`ports/model_provider.py` ("`ModelProvider.generate(schema, prompt, budget)
-> StructuredOutput` Protocol. Schema-enforced output only."), §1 "provider
neutrality" (cloud LLM/local LLM/external MCP client attach through the
same contract, per-tenant cost caps).

Adapters implementing this Protocol: `anthropic_provider.py` (AI-6),
`openai_compatible_provider.py` (AI-7), `gemini_provider.py` (AI-7b) --
none exist yet. `domain/`/`application/` callers (AI-9's
`generate_proposal`, not yet implemented) depend on this Protocol only,
never on a concrete adapter class -- this module imports nothing from the
adapters it describes (adapters depend on the port, not the reverse).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate


@dataclass(frozen=True)
class GenerationBudget:
    """What a single `generate()` call is allowed to cost. `cost_cap` is
    the per-call slice of an already-approved `domain/budget.TenantBudget`
    reservation (`TenantBudget.reserve()`) -- enforcing the tenant/token
    daily cap itself happens *before* `generate()` is ever invoked; this
    type only carries the resulting allowance down into the adapter so it
    can size its request (e.g. `max_tokens`) and refuse to call the
    upstream API at all if the estimated cost would exceed `cost_cap`."""

    cost_cap: Decimal
    max_output_tokens: int

    def __post_init__(self) -> None:
        if self.cost_cap < 0:
            raise ValueError("cost_cap must be >= 0")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")


@dataclass(frozen=True)
class StructuredOutput:
    """The only shape a `ModelProvider.generate()` call may return -- free
    text is never a valid result (spec §1 "deterministic gate", §3 proposal
    schema "unknown fields rejected"). `data` is expected to already satisfy the JSON
    Schema passed as `schema`; validating that against the schema is the
    caller's job (AI-8's `domain/schema.py`, not yet implemented) -- this
    port does not reimplement JSON Schema validation. `prompt_hash` and
    `cost` let the caller record the reproducibility key component and the
    actual spend to reconcile against `GenerationBudget.cost_cap`."""

    data: dict[str, Any]
    prompt_hash: str
    cost: Decimal
    output_tokens: int

    def __post_init__(self) -> None:
        if self.cost < 0:
            raise ValueError("cost must be >= 0")
        if self.output_tokens < 0:
            raise ValueError("output_tokens must be >= 0")


@runtime_checkable
class ModelProvider(Protocol):
    """§2.2 AI-5 row verbatim: `ModelProvider.generate(schema, prompt,
    budget) -> StructuredOutput`. Every built-in adapter (AI-6/7/7b) and
    any future provider implements exactly this shape (§1 "provider
    neutrality" -- cloud LLM, local LLM, and external MCP clients all
    attach through the same contract)."""

    async def generate(
        self,
        schema: dict[str, Any],
        prompt: PromptTemplate,
        budget: GenerationBudget,
    ) -> StructuredOutput: ...
