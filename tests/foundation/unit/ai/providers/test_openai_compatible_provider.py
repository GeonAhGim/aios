"""Contract tests (fake transport) for `src/foundation/ai/providers/adapters/
openai_compatible_provider.py` -- task-2641 AI-7 ("openai_compatible_provider
(OpenAI/Codex·로컬 LLM) + 계약 테스트(fake)").

No real OpenAI-compatible API call is made anywhere in this file: every test
injects `httpx.MockTransport` the same way
`tests/foundation/unit/ai/providers/test_anthropic_provider.py` fakes the
Anthropic Messages API -- monkeypatch the adapter module's
`httpx.AsyncClient` to a factory that pins `transport=`. No OpenAI SDK
package is ever imported; this adapter talks to the documented
`/chat/completions` HTTP contract directly (module docstring), so no package
needs to be installed for these tests to exercise the real code path.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.foundation.ai.providers.adapters.openai_compatible_provider import (
    OpenAICompatibleCostCapExceededError,
    OpenAICompatiblePricing,
    OpenAICompatibleProvider,
    OpenAICompatibleStructuredOutputError,
    OpenAICompatibleUpstreamError,
)
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, ModelProvider

_SCHEMA = {
    "type": "object",
    "properties": {"hypothesis": {"type": "string"}},
    "required": ["hypothesis"],
}
_PRICING = OpenAICompatiblePricing(
    input_cost_per_token=Decimal("0.000003"), output_cost_per_token=Decimal("0.000015")
)


def _prompt(template: str = "propose a mean-reversion strategy") -> PromptTemplate:
    return PromptTemplate(prompt_id="strategy.propose", version=1, template=template)


def _budget(*, cost_cap: str = "1.00", max_output_tokens: int = 1000) -> GenerationBudget:
    return GenerationBudget(cost_cap=Decimal(cost_cap), max_output_tokens=max_output_tokens)


def _provider(**kwargs: Any) -> OpenAICompatibleProvider:
    defaults: dict[str, Any] = {
        "api_key": "sk-test",
        "model": "gpt-test-model",
        "pricing": _PRICING,
    }
    defaults.update(kwargs)
    return OpenAICompatibleProvider(**defaults)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    import src.foundation.ai.providers.adapters.openai_compatible_provider as adapter_module

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _factory)


def _tool_call_response(
    data: dict[str, Any], *, prompt_tokens: int = 100, completion_tokens: int = 50
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "structured_output",
                                    "arguments": json.dumps(data),
                                },
                            }
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


# --- 정상 경로: 구조적 적합성 + 계약(AI-6과 동일 산출물) ---


def test_provider_satisfies_model_provider_protocol() -> None:
    assert isinstance(_provider(), ModelProvider)


async def test_generate_returns_structured_output_with_measured_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _tool_call_response(
            {"hypothesis": "buy the dip"}, prompt_tokens=100, completion_tokens=50
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()
    prompt = _prompt()
    budget = _budget()

    result = await provider.generate(_SCHEMA, prompt, budget)

    assert result.data == {"hypothesis": "buy the dip"}
    assert result.output_tokens == 50
    assert (
        result.cost
        == Decimal("100") * _PRICING.input_cost_per_token
        + Decimal("50") * _PRICING.output_cost_per_token
    )
    assert result.prompt_hash == prompt_hash(prompt)

    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = req.content.decode("utf-8")
    assert '"tool_choice"' in body and '"structured_output"' in body
    assert '"max_tokens":1000' in body.replace(" ", "")


async def test_generate_honors_custom_base_url_for_local_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "로컬 LLM" (§1 provider neutrality): 고정 호스트가 아니라 생성자
    `base_url`로 자체 호스팅 서버(vLLM/llama.cpp/Ollama 등)를 가리킬 수 있어야
    한다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _tool_call_response({"hypothesis": "local"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider(base_url="http://localhost:11434/v1")

    await provider.generate(_SCHEMA, _prompt(), _budget())

    assert str(captured[0].url) == "http://localhost:11434/v1/chat/completions"


# --- 부정 테스트 (>=3) ---


def test_empty_api_key_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(api_key="")


def test_empty_model_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(model="")


def test_empty_base_url_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(base_url="")


def test_negative_pricing_rate_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        OpenAICompatiblePricing(
            input_cost_per_token=Decimal("-0.01"), output_cost_per_token=Decimal("0")
        )


async def test_over_cap_estimate_refuses_upstream_call_before_spending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight refusal: a cap far below the cheapest possible request must
    never reach the network -- `captured` staying empty proves no money could
    have been spent, not just that an exception was raised."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _tool_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()
    budget = _budget(cost_cap="0.0000001", max_output_tokens=1000)

    with pytest.raises(OpenAICompatibleCostCapExceededError):
        await provider.generate(_SCHEMA, _prompt(), budget)

    assert captured == []


async def test_http_4xx_raises_upstream_error_with_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_api_key: incorrect API key provided")

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(OpenAICompatibleUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code == 401
    assert "incorrect API key" in str(exc_info.value)


# --- 실패 주입: 상류 응답이 손상돼도 자유 텍스트로 조용히 대체하지 않는다 ---


async def test_failure_injection_missing_tool_calls_is_rejected_not_treated_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 모델이 도구 호출을 거부하고 자유 텍스트만 반환하는 손상된
    응답을 주입한다. §1 "free text is never a valid result"가 실제로 지켜지는지
    -- 조용히 텍스트를 감싸서 반환하지 않고 fail-closed 거부하는지 증명한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "I would rather not call a tool."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(OpenAICompatibleStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_failure_injection_wrong_tool_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 변형: 다른 함수 이름의 tool_call(예: 상류 프록시/모델 버전
    혼선)을 절대 우리 스키마의 결과로 오인해 받아들이지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "some_other_tool",
                                        "arguments": json.dumps({"x": 1}),
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(OpenAICompatibleStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_failure_injection_malformed_json_arguments_string_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: OpenAI 호환 계약 고유의 실패 모드 -- `function.arguments`는
    이미 파싱된 객체(Anthropic의 `tool_use.input`)가 아니라 JSON 인코딩된
    문자열이다. 일부 로컬 LLM 서버는 이 문자열을 깨진 JSON으로 반환할 수 있으므로
    -- 디코딩 실패 시 예외 없이 조용히 넘어가지 않고 fail-closed 거부해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "structured_output",
                                        "arguments": "{not valid json",
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(OpenAICompatibleStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_network_error_raises_upstream_error_with_no_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(OpenAICompatibleUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code is None


# --- 성능 단언 + 게이트 적색 재현 ---

_OVERHEAD_BUDGET_SECONDS = 0.05


@pytest.mark.perf
async def test_generate_overhead_p95_under_budget_with_instant_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성능 단언: 네트워크 자체를 즉시 응답으로 고정한 상태에서, 어댑터가 직접
    수행하는 작업(사전 비용 추정·페이로드 직렬화·usage 파싱·JSON 인자 디코딩·
    비용/해시 계산)의 p95 지연이 예산을 넘지 않아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()
    prompt = _prompt()
    budget = _budget()

    durations: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        await provider.generate(_SCHEMA, prompt, budget)
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95)]
    assert p95 < _OVERHEAD_BUDGET_SECONDS, (
        f"p95 {p95:.4f}s exceeds budget {_OVERHEAD_BUDGET_SECONDS}s"
    )


@pytest.mark.perf
async def test_gate_red_repro_injected_transport_latency_breaches_the_same_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 성능 단언이 실제로 지연을 감지하는지(항상 통과하는
    타우톨로지가 아닌지) 증명한다. 전송에 예산을 초과하는 지연을 주입하면 같은
    측정 방식이 예산 초과를 실제로 잡아낸다."""
    injected_delay = _OVERHEAD_BUDGET_SECONDS * 3

    def slow_handler(request: httpx.Request) -> httpx.Response:
        time.sleep(injected_delay)
        return _tool_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(slow_handler))
    provider = _provider()

    start = time.perf_counter()
    await provider.generate(_SCHEMA, _prompt(), _budget())
    elapsed = time.perf_counter() - start

    assert elapsed >= _OVERHEAD_BUDGET_SECONDS, (
        "적색 재현 실패 — 주입한 지연이 측정에 반영되지 않았다(타우톨로지 위험)"
    )
