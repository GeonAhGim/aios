"""External-agent `ModelProvider` -- AI-16.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2
(`adapters/external_agent_provider.py`: "any MCP client (Claude Code/Codex
CLI/Gemini CLI) attaches with an agent token and calls the read/research/
propose/paper tools -- not a model provider, a proposal channel; the
pipeline is complete even without a built-in model"), §9 AI-16 DoD.

Every other `ModelProvider` adapter (AI-6/7/7b, none shipped yet) calls an
upstream LLM API and turns its free-form response into `StructuredOutput`.
This adapter has no upstream to call: the "generation" already happened
outside AIOS entirely, in whatever external agent process is calling
`src/api/mcp/tools_propose.py`'s `submit_proposal` tool over HTTP with an
already-drafted `script_source`/`hypothesis`/`data_scope`/`params` payload.
`generate()` therefore does no I/O and cannot fail on a schema violation --
it only echoes the payload it was constructed with back out as
`StructuredOutput.data`; `domain/proposal_rules.py::validate_schema`
(already the sole schema authority, `evaluate_proposal_candidate`'s first
check) is what actually rejects a malformed draft, one step later in
`generate_proposal`'s own pipeline -- this adapter does not re-validate
against `schema` itself and must not, the same "do not re-implement a
check another leaf already owns" discipline every other AI leaf's docstring
states.

`cost`/`output_tokens` are always zero -- there is no metered upstream call
this adapter's cost governs, so `domain/budget.py`'s reservation is a no-op
for the external-agent channel by construction, not by this adapter
special-casing it."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput

__all__ = ["EXTERNAL_AGENT_PROMPT", "ExternalAgentProvider"]

EXTERNAL_AGENT_PROMPT = PromptTemplate(
    prompt_id="external_agent_passthrough",
    version=1,
    template="external agent submitted an already-drafted proposal; no prompt was rendered",
)


class ExternalAgentProvider:
    """`ModelProvider` Protocol implementation for `ProviderRef.EXTERNAL_AGENT`.
    `draft_payload` is the caller-supplied dict this instance always returns
    from `generate()`, regardless of `schema`/`prompt`/`budget` -- there is
    no generation step to parameterize."""

    def __init__(self, draft_payload: dict[str, Any]) -> None:
        self._draft_payload = draft_payload

    async def generate(
        self,
        schema: dict[str, Any],
        prompt: PromptTemplate,
        budget: GenerationBudget,
    ) -> StructuredOutput:
        return StructuredOutput(
            data=self._draft_payload,
            prompt_hash=prompt_hash(EXTERNAL_AGENT_PROMPT),
            cost=Decimal(0),
            output_tokens=0,
        )
