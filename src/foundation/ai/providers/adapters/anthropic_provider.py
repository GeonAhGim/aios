"""Anthropic provider adapter -- structured output via forced tool-use, cost metering.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-6 row --
`anthropic_provider.py`: Claude API, structured output, cost metering.
Implements the `ModelProvider` Protocol (AI-5, `ports/model_provider.py`)
against Anthropic's public Messages API (`POST /v1/messages`, versioned via
the `anthropic-version` header -- a documented, stable contract, not an
unverified fact). This adapter calls that HTTP endpoint directly with `httpx`
rather than the `anthropic` PyPI package: AI-3 already cleared the package for
import (docs/design/AI_DEPENDENCIES_EVAL.md §1.2, verdict: importable, no
blocking condition), but it is not installed in this venv and one HTTP call
needs nothing beyond what
`src/foundation/risk/adapters/telegram_adapter.py` already does for a simpler
API -- same "hand-rolled httpx client, no vendor SDK" precedent as bitget/kis/nh.

Structured output (§1 "free text is never a valid result"): the Messages API has
no `response_format` field -- schema enforcement goes through Anthropic's
documented forced tool-use pattern instead: a single tool (`_TOOL_NAME`) whose
`input_schema` is the caller's JSON Schema, with `tool_choice` pinned to that
tool so the model can only reply by calling it. A response lacking that exact
tool_use block is rejected fail-closed (`AnthropicStructuredOutputError`) rather
than falling back to free text.

Cost measurement (§2.2 "cost metering"): actual cost always comes from the response's
`usage.{input,output}_tokens` (present on every /v1/messages reply) multiplied by
`AnthropicPricing` -- a rate table the caller supplies and owns, never hardcoded
here. Anthropic's published $/token rates drift over time and differ per model;
baking a guessed snapshot into this module would silently go stale, the same
reason `GenerationBudget.cost_cap` is already an external input rather than a
constant this module invents. The one estimate this module does make --
`_estimate_input_tokens` -- is a deliberately conservative pre-flight heuristic
(chars/4) used only to refuse an over-cap call *before* spending anything
(`ports/model_provider.py` docstring); it never substitutes for the real
post-call `usage` figures in the returned `StructuredOutput.cost`.

The pre-flight estimate is computed over the full request payload the wire call
actually sends -- `prompt.template` *and* the JSON-serialized `schema` (the
`tools[0].input_schema` block, forced via `tool_choice`) -- not `prompt.template`
alone. Anthropic bills the whole `/v1/messages` request body as input tokens;
counting only the prompt text under-estimates the pre-flight cost by the size of
the caller's schema, which for a nontrivial JSON Schema is not negligible and
would let a call through that the schema's real weight should have refused
before spending anything (the exact failure `AnthropicCostCapExceededError`
exists to prevent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput

_API_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_TOOL_NAME = "structured_output"
_CHARS_PER_TOKEN_ESTIMATE = 4


class AnthropicProviderError(Exception):
    """Common base for this adapter's failures."""


class AnthropicCostCapExceededError(AnthropicProviderError):
    """Raised before any HTTP call -- the pre-flight cost estimate already
    exceeds `GenerationBudget.cost_cap`, so the upstream API is never invoked
    (§2.2 port docstring: "refuse to call the upstream API at all")."""

    def __init__(self, *, estimated: Decimal, cap: Decimal) -> None:
        self.estimated = estimated
        self.cap = cap
        super().__init__(f"estimated cost {estimated} exceeds cap {cap} -- upstream call refused")


class AnthropicUpstreamError(AnthropicProviderError):
    """Network failure or HTTP error status from the Messages API. Maps to
    spec §3 `AI_PROVIDER_UNAVAILABLE` (503) at the application layer that
    calls this adapter (not yet implemented, AI-9)."""

    def __init__(self, *, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class AnthropicStructuredOutputError(AnthropicProviderError):
    """The response did not contain the forced `_TOOL_NAME` tool_use block --
    fail closed rather than guessing at a free-text reply (§1)."""


@dataclass(frozen=True)
class AnthropicPricing:
    """$/token rate table the caller owns and updates -- see module docstring
    for why this adapter never hardcodes Anthropic's published rates."""

    input_cost_per_token: Decimal
    output_cost_per_token: Decimal

    def __post_init__(self) -> None:
        if self.input_cost_per_token < 0:
            raise ValueError("input_cost_per_token must be >= 0")
        if self.output_cost_per_token < 0:
            raise ValueError("output_cost_per_token must be >= 0")


def _estimate_input_tokens(text: str) -> int:
    """Conservative pre-flight heuristic only -- see module docstring."""
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def _estimate_request_input_tokens(prompt: PromptTemplate, schema: dict[str, Any]) -> int:
    """Pre-flight estimate over everything the wire call bills as input --
    the prompt text *and* the forced tool's `input_schema` (module docstring:
    undercounting the schema would let an over-cap call through)."""
    return _estimate_input_tokens(prompt.template) + _estimate_input_tokens(json.dumps(schema))


def _extract_tool_input(body: dict[str, Any]) -> dict[str, Any]:
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            if block.get("name") != _TOOL_NAME:
                raise AnthropicStructuredOutputError(
                    f"unexpected tool_use name {block.get('name')!r}, expected {_TOOL_NAME!r}"
                )
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return tool_input
            raise AnthropicStructuredOutputError("tool_use.input was not a JSON object")
    raise AnthropicStructuredOutputError(
        f"response contained no {_TOOL_NAME!r} tool_use block -- refusing free-text fallback"
    )


class AnthropicProvider:
    """`ModelProvider` adapter for Anthropic's Messages API. Satisfies the
    Protocol structurally (see `ports/model_provider.py`); does not inherit
    from it."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        pricing: AnthropicPricing,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not model:
            raise ValueError("model must not be empty")
        self._api_key = api_key
        self._model = model
        self._pricing = pricing
        self._timeout = timeout

    async def generate(
        self,
        schema: dict[str, Any],
        prompt: PromptTemplate,
        budget: GenerationBudget,
    ) -> StructuredOutput:
        estimated_cost = (
            Decimal(_estimate_request_input_tokens(prompt, schema))
            * self._pricing.input_cost_per_token
            + Decimal(budget.max_output_tokens) * self._pricing.output_cost_per_token
        )
        if estimated_cost > budget.cost_cap:
            raise AnthropicCostCapExceededError(estimated=estimated_cost, cap=budget.cost_cap)

        payload = {
            "model": self._model,
            "max_tokens": budget.max_output_tokens,
            "messages": [{"role": "user", "content": prompt.template}],
            "tools": [
                {
                    "name": _TOOL_NAME,
                    "description": "Return the structured result matching the given schema.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(base_url=_API_BASE, timeout=self._timeout) as client:
                response = await client.post("/v1/messages", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AnthropicUpstreamError(status_code=None, message=str(exc)) from exc

        if response.status_code >= 400:
            raise AnthropicUpstreamError(
                status_code=response.status_code, message=response.text[:300]
            )

        body = response.json()
        data = _extract_tool_input(body)
        usage = body.get("usage") or {}
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
        cost = (
            Decimal(input_tokens) * self._pricing.input_cost_per_token
            + Decimal(output_tokens) * self._pricing.output_cost_per_token
        )
        return StructuredOutput(
            data=data,
            prompt_hash=prompt_hash(prompt),
            cost=cost,
            output_tokens=output_tokens,
        )
