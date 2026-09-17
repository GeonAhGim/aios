"""Gemini provider adapter -- structured output via forced function-calling,
cost metering.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-7b row --
`gemini_provider.py`: Google Gemini API, DoD "identical artifact shape" (same
artifact shape as AI-6's `AnthropicProvider` and AI-7's
`OpenAICompatibleProvider` -- all three satisfy the `ModelProvider` Protocol
from AI-5's `ports/model_provider.py` and must be interchangeable to a
caller). This adapter targets Google's public, documented `generateContent`
REST contract (`POST {base_url}/v1beta/models/{model}:generateContent`,
`x-goog-api-key: <key>` header -- the documented alternative to the `?key=`
query parameter, used here so the API key never lands in a URL that a proxy
or access log might record, consistent with CLAUDE.md "never print secrets")
-- a stable, publicly documented contract, not an unverified fact, same
confidence level as `anthropic_provider.py`'s `/v1/messages` and
`openai_compatible_provider.py`'s `/chat/completions`. Like both of those,
this calls the HTTP endpoint directly with `httpx` rather than the
`google-genai` SDK package -- same "hand-rolled httpx client, no vendor SDK"
precedent as bitget/kis/nh/anthropic_provider.py/openai_compatible_provider.py
-- no SDK import needed for one documented HTTP call. Gemini has no
self-hosted/local-LLM variant (unlike AI-7's OpenAI-compatible contract), so
`base_url` is a fixed module constant here, same posture as
`anthropic_provider.py` rather than AI-7's constructor parameter.

Structured output (§1 "free text is never a valid result"): `generateContent`
has no schema-enforcing response field portable the way Anthropic's
`tool_choice` is, but Gemini's documented function-calling contract has an
equivalent forced-call mode -- a single function declaration (`_TOOL_NAME`)
whose `parameters` is the caller's JSON Schema, with `toolConfig.
functionCallingConfig` pinned to `mode: "ANY"` and `allowedFunctionNames:
[_TOOL_NAME]` so the model can only reply by calling it. Unlike AI-7's OpenAI
contract (`function.arguments` as a JSON-encoded *string*), Gemini's
documented `functionCall.args` field is already a parsed JSON object -- same
already-parsed shape as Anthropic's `tool_use.input`. A response that omits
the forced function call, names a different function, or whose `args` is not
a JSON object is rejected fail-closed (`GeminiStructuredOutputError`) rather
than falling back to free text, same contract as the other two adapters.

Cost measurement (§2.2 "cost metering", DoD "identical artifact shape"): actual cost always
comes from the response's `usageMetadata.{promptTokenCount,
candidatesTokenCount}` (the documented Gemini field names, present on every
`generateContent` reply) multiplied by `GeminiPricing` -- a rate table the
caller supplies and owns, never hardcoded here, for the same reason
`AnthropicPricing`/`OpenAICompatiblePricing` are caller-owned: published
$/token rates drift and differ per model. `_estimate_input_tokens` is the
same deliberately conservative pre-flight heuristic (chars/4) used only to
refuse an over-cap call *before* spending anything; it never substitutes for
the real post-call `usageMetadata` figures in the returned
`StructuredOutput.cost`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput

_API_BASE = "https://generativelanguage.googleapis.com"
_API_VERSION_PATH = "v1beta"
_TOOL_NAME = "structured_output"
_CHARS_PER_TOKEN_ESTIMATE = 4


class GeminiProviderError(Exception):
    """Common base for this adapter's failures."""


class GeminiCostCapExceededError(GeminiProviderError):
    """Raised before any HTTP call -- the pre-flight cost estimate already
    exceeds `GenerationBudget.cost_cap`, so the upstream API is never invoked
    (§2.2 port docstring: "refuse to call the upstream API at all"), same
    pre-flight contract as `AnthropicCostCapExceededError` /
    `OpenAICompatibleCostCapExceededError`."""

    def __init__(self, *, estimated: Decimal, cap: Decimal) -> None:
        self.estimated = estimated
        self.cap = cap
        super().__init__(f"estimated cost {estimated} exceeds cap {cap} -- upstream call refused")


class GeminiUpstreamError(GeminiProviderError):
    """Network failure or HTTP error status from the `generateContent`
    endpoint. Maps to spec §3 `AI_PROVIDER_UNAVAILABLE` (503) at the
    application layer that calls this adapter (not yet implemented, AI-9)."""

    def __init__(self, *, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class GeminiStructuredOutputError(GeminiProviderError):
    """The response did not contain a valid forced `_TOOL_NAME` function call
    with JSON-object args -- fail closed rather than guessing at a free-text
    reply (§1), same contract as `AnthropicStructuredOutputError` /
    `OpenAICompatibleStructuredOutputError`."""


@dataclass(frozen=True)
class GeminiPricing:
    """$/token rate table the caller owns and updates -- see module
    docstring for why this adapter never hardcodes a provider's published
    rates."""

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


def _extract_function_call_args(body: dict[str, Any]) -> dict[str, Any]:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GeminiStructuredOutputError(
            "response contained no candidates -- refusing free-text fallback"
        )
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise GeminiStructuredOutputError(
            f"response contained no {_TOOL_NAME!r} function call -- refusing free-text fallback"
        )
    function_call = next(
        (
            part.get("functionCall")
            for part in parts
            if isinstance(part, dict) and part.get("functionCall")
        ),
        None,
    )
    if not isinstance(function_call, dict):
        raise GeminiStructuredOutputError(
            f"response contained no {_TOOL_NAME!r} function call -- refusing free-text fallback"
        )
    if function_call.get("name") != _TOOL_NAME:
        raise GeminiStructuredOutputError(
            f"unexpected function call name {function_call.get('name')!r}, expected {_TOOL_NAME!r}"
        )
    args = function_call.get("args")
    if not isinstance(args, dict):
        raise GeminiStructuredOutputError("functionCall.args was not a JSON object")
    return args


class GeminiProvider:
    """`ModelProvider` adapter for Google's Gemini `generateContent` API.
    Satisfies the Protocol structurally (see `ports/model_provider.py`);
    does not inherit from it -- same "not yet installed as a package, one
    HTTP call" posture as `AnthropicProvider` / `OpenAICompatibleProvider`."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        pricing: GeminiPricing,
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
            Decimal(_estimate_input_tokens(prompt.template)) * self._pricing.input_cost_per_token
            + Decimal(budget.max_output_tokens) * self._pricing.output_cost_per_token
        )
        if estimated_cost > budget.cost_cap:
            raise GeminiCostCapExceededError(estimated=estimated_cost, cap=budget.cost_cap)

        payload = {
            "contents": [{"parts": [{"text": prompt.template}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": _TOOL_NAME,
                            "description": "Return the structured result matching the schema.",
                            "parameters": schema,
                        }
                    ]
                }
            ],
            "toolConfig": {
                "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [_TOOL_NAME]}
            },
            "generationConfig": {"maxOutputTokens": budget.max_output_tokens},
        }
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }
        path = f"/{_API_VERSION_PATH}/models/{self._model}:generateContent"
        try:
            async with httpx.AsyncClient(base_url=_API_BASE, timeout=self._timeout) as client:
                response = await client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise GeminiUpstreamError(status_code=None, message=str(exc)) from exc

        if response.status_code >= 400:
            raise GeminiUpstreamError(status_code=response.status_code, message=response.text[:300])

        body = response.json()
        data = _extract_function_call_args(body)
        usage = body.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount", 0))
        completion_tokens = int(usage.get("candidatesTokenCount", 0))
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
