"""OpenAI-compatible provider adapter -- OpenAI/Codex and self-hosted local
LLM servers, structured output via forced function-calling, cost metering.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-7 row --
`openai_compatible_provider.py`: "OpenAI/Codex, local LLM", DoD "identical
artifact shape" (same artifact shape as AI-6's `AnthropicProvider` -- both satisfy the
`ModelProvider` Protocol from AI-5's `ports/model_provider.py` and must be
interchangeable to a caller). This adapter targets the OpenAI Chat
Completions wire contract (`POST {base_url}/chat/completions`,
`Authorization: Bearer <key>`) -- the de facto contract that OpenAI itself,
Codex-style gateways, and self-hosted local LLM servers (vLLM, llama.cpp
server, Ollama's OpenAI-compat endpoint, LM Studio) all implement, which is
why `base_url` is a constructor parameter here rather than the fixed host
`anthropic_provider.py` uses -- "local LLM" (§1 "provider neutrality") means
there is no single fixed host to hardcode. Like the Anthropic adapter, this
calls that HTTP endpoint directly with `httpx` rather than an SDK package
(same "hand-rolled httpx client, no vendor SDK" precedent as
bitget/kis/nh/anthropic_provider.py) -- no OpenAI SDK import needed for one
documented HTTP call.

Structured output (§1 "free text is never a valid result"): the Chat
Completions contract has no schema-enforcing field portable across every
OpenAI-compatible server, but forced function-calling is -- a single tool
(`_TOOL_NAME`) whose `parameters` is the caller's JSON Schema, with
`tool_choice` pinned to that tool so the model can only reply by calling it.
Unlike Anthropic's `tool_use.input` (already a parsed JSON object), this
contract returns `function.arguments` as a *JSON-encoded string*
(OpenAI's documented wire shape) -- decoding it is this adapter's job, and a
response that skips the forced tool call, names a different tool, or whose
`arguments` string fails to decode to a JSON object is rejected fail-closed
(`OpenAICompatibleStructuredOutputError`) rather than falling back to free
text, same as `anthropic_provider.py`.

Cost measurement (§2.2 "cost metering", DoD "identical artifact shape"): actual cost always
comes from the response's `usage.{prompt,completion}_tokens` (the
OpenAI-compatible field names, present on every `/chat/completions` reply)
multiplied by `OpenAICompatiblePricing` -- a rate table the caller supplies
and owns, never hardcoded here, for the same reason
`anthropic_provider.py`'s `AnthropicPricing` is caller-owned: published
$/token rates (and a self-hosted server's effective rate, which may be zero)
drift and differ per model/deployment. `_estimate_input_tokens` is the same
deliberately conservative pre-flight heuristic (chars/4) used only to refuse
an over-cap call *before* spending anything; it never substitutes for the
real post-call `usage` figures in the returned `StructuredOutput.cost`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_TOOL_NAME = "structured_output"
_CHARS_PER_TOKEN_ESTIMATE = 4


class OpenAICompatibleProviderError(Exception):
    """Common base for this adapter's failures."""


class OpenAICompatibleCostCapExceededError(OpenAICompatibleProviderError):
    """Raised before any HTTP call -- the pre-flight cost estimate already
    exceeds `GenerationBudget.cost_cap`, so the upstream API is never invoked
    (§2.2 port docstring: "refuse to call the upstream API at all"), same
    pre-flight contract as `AnthropicCostCapExceededError`."""

    def __init__(self, *, estimated: Decimal, cap: Decimal) -> None:
        self.estimated = estimated
        self.cap = cap
        super().__init__(f"estimated cost {estimated} exceeds cap {cap} -- upstream call refused")


class OpenAICompatibleUpstreamError(OpenAICompatibleProviderError):
    """Network failure or HTTP error status from the Chat Completions
    endpoint. Maps to spec §3 `AI_PROVIDER_UNAVAILABLE` (503) at the
    application layer that calls this adapter (not yet implemented, AI-9)."""

    def __init__(self, *, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class OpenAICompatibleStructuredOutputError(OpenAICompatibleProviderError):
    """The response did not contain a valid forced `_TOOL_NAME` tool call
    with JSON-object arguments -- fail closed rather than guessing at a
    free-text reply (§1), same contract as `AnthropicStructuredOutputError`."""


@dataclass(frozen=True)
class OpenAICompatiblePricing:
    """$/token rate table the caller owns and updates -- see module
    docstring for why this adapter never hardcodes a provider's published
    rates (and why a self-hosted deployment's rate may legitimately be
    zero)."""

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


def _extract_tool_call_arguments(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAICompatibleStructuredOutputError(
            "response contained no choices -- refusing free-text fallback"
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(tool_calls, list) or not tool_calls:
        raise OpenAICompatibleStructuredOutputError(
            f"response contained no {_TOOL_NAME!r} tool call -- refusing free-text fallback"
        )
    function = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
    if not isinstance(function, dict):
        raise OpenAICompatibleStructuredOutputError("tool_call had no function payload")
    if function.get("name") != _TOOL_NAME:
        raise OpenAICompatibleStructuredOutputError(
            f"unexpected tool call name {function.get('name')!r}, expected {_TOOL_NAME!r}"
        )
    arguments_raw = function.get("arguments")
    if not isinstance(arguments_raw, str):
        raise OpenAICompatibleStructuredOutputError("function.arguments was not a JSON string")
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        raise OpenAICompatibleStructuredOutputError(
            f"function.arguments was not valid JSON: {exc}"
        ) from exc
    if not isinstance(arguments, dict):
        raise OpenAICompatibleStructuredOutputError(
            "function.arguments did not decode to a JSON object"
        )
    return arguments


class OpenAICompatibleProvider:
    """`ModelProvider` adapter for the OpenAI-compatible Chat Completions
    contract (OpenAI/Codex-style gateways and self-hosted local LLM
    servers). Satisfies the Protocol structurally (see
    `ports/model_provider.py`); does not inherit from it -- same "not yet
    installed as a package, one HTTP call" posture as `AnthropicProvider`."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        pricing: OpenAICompatiblePricing,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not model:
            raise ValueError("model must not be empty")
        if not base_url:
            raise ValueError("base_url must not be empty")
        self._api_key = api_key
        self._model = model
        self._pricing = pricing
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate(
        self,
        schema: dict[str, Any],
        prompt: PromptTemplate,
        budget: GenerationBudget,
    ) -> StructuredOutput:
        estimated_cost = (
            Decimal(_estimate_input_tokens(prompt.template)) * self._pricing.input_cost_per_token
            + Decimal(budget.max_output_tokens) * self._pricing.output_cost_per_token
        )
        if estimated_cost > budget.cost_cap:
            raise OpenAICompatibleCostCapExceededError(
                estimated=estimated_cost, cap=budget.cost_cap
            )

        payload = {
            "model": self._model,
            "max_tokens": budget.max_output_tokens,
            "messages": [{"role": "user", "content": prompt.template}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": _TOOL_NAME,
                        "description": "Return the structured result matching the given schema.",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise OpenAICompatibleUpstreamError(status_code=None, message=str(exc)) from exc

        if response.status_code >= 400:
            raise OpenAICompatibleUpstreamError(
                status_code=response.status_code, message=response.text[:300]
            )

        body = response.json()
        data = _extract_tool_call_arguments(body)
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        cost = (
            Decimal(prompt_tokens) * self._pricing.input_cost_per_token
            + Decimal(completion_tokens) * self._pricing.output_cost_per_token
        )
        return StructuredOutput(
            data=data,
            prompt_hash=prompt_hash(prompt),
            cost=cost,
            output_tokens=completion_tokens,
        )
