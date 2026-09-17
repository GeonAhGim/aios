"""Contract tests (fake transport) for `src/foundation/ai/providers/adapters/
gemini_provider.py` -- task-2642 AI-7b ("gemini_provider + 계약 테스트(fake)").

No real Gemini API call is made anywhere in this file: every test injects
`httpx.MockTransport` the same way `tests/foundation/unit/ai/providers/
test_anthropic_provider.py` / `test_openai_compatible_provider.py` fake their
respective APIs -- monkeypatch the adapter module's `httpx.AsyncClient` to a
factory that pins `transport=`. The `google-genai` SDK package is never
imported; this adapter talks to the documented `generateContent` HTTP
contract directly (module docstring), so no package needs to be installed
for these tests to exercise the real code path.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.foundation.ai.providers.adapters.gemini_provider import (
    GeminiCostCapExceededError,
    GeminiPricing,
    GeminiProvider,
    GeminiStructuredOutputError,
    GeminiUpstreamError,
)
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, ModelProvider

_SCHEMA = {
    "type": "object",
    "properties": {"hypothesis": {"type": "string"}},
    "required": ["hypothesis"],
}
_PRICING = GeminiPricing(
    input_cost_per_token=Decimal("0.000003"), output_cost_per_token=Decimal("0.000015")
)


def _prompt(template: str = "propose a mean-reversion strategy") -> PromptTemplate:
    return PromptTemplate(prompt_id="strategy.propose", version=1, template=template)


def _budget(*, cost_cap: str = "1.00", max_output_tokens: int = 1000) -> GenerationBudget:
    return GenerationBudget(cost_cap=Decimal(cost_cap), max_output_tokens=max_output_tokens)


def _provider(**kwargs: Any) -> GeminiProvider:
    defaults: dict[str, Any] = {
        "api_key": "gm-test",
        "model": "gemini-test-model",
        "pricing": _PRICING,
    }
    defaults.update(kwargs)
    return GeminiProvider(**defaults)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    import src.foundation.ai.providers.adapters.gemini_provider as adapter_module

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _factory)


def _function_call_response(
    data: dict[str, Any], *, prompt_tokens: int = 100, completion_tokens: int = 50
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "structured_output", "args": data}}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": completion_tokens,
            },
        },
    )


# --- 정상 경로: 구조적 적합성 + 계약(AI-6/AI-7과 동일 산출물) ---


def test_provider_satisfies_model_provider_protocol() -> None:
    assert isinstance(_provider(), ModelProvider)


async def test_generate_returns_structured_output_with_measured_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _function_call_response(
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
    assert str(req.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test-model:generateContent"
    )
    assert req.headers["x-goog-api-key"] == "gm-test"
    body = req.content.decode("utf-8")
    assert '"functionCallingConfig"' in body and '"structured_output"' in body
    assert '"maxOutputTokens":1000' in body.replace(" ", "")


async def test_api_key_never_appears_in_request_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """`x-goog-api-key` 헤더를 쓰는 이유(모듈 docstring): 키가 `?key=` 쿼리
    파라미터로 URL에 실려 프록시/접근 로그에 남지 않아야 한다(CLAUDE.md
    "비밀 값을 로그에 출력하지 않는다")."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _function_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider(api_key="super-secret-key")

    await provider.generate(_SCHEMA, _prompt(), _budget())

    assert "super-secret-key" not in str(captured[0].url)


# --- 부정 테스트 (>=3) ---


def test_empty_api_key_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(api_key="")


def test_empty_model_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(model="")


def test_negative_pricing_rate_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        GeminiPricing(input_cost_per_token=Decimal("-0.01"), output_cost_per_token=Decimal("0"))


async def test_over_cap_estimate_refuses_upstream_call_before_spending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight refusal: a cap far below the cheapest possible request must
    never reach the network -- `captured` staying empty proves no money could
    have been spent, not just that an exception was raised."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _function_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()
    budget = _budget(cost_cap="0.0000001", max_output_tokens=1000)

    with pytest.raises(GeminiCostCapExceededError):
        await provider.generate(_SCHEMA, _prompt(), budget)

    assert captured == []


async def test_http_4xx_raises_upstream_error_with_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="API key not valid. Please pass a valid API key.")

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(GeminiUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code == 401
    assert "API key not valid" in str(exc_info.value)


# --- 실패 주입: 상류 응답이 손상돼도 자유 텍스트로 조용히 대체하지 않는다 ---


async def test_failure_injection_missing_function_call_is_rejected_not_treated_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 모델이 함수 호출을 거부하고 자유 텍스트만 반환하는 손상된
    응답을 주입한다. §1 "free text is never a valid result"가 실제로 지켜지는지
    -- 조용히 텍스트를 감싸서 반환하지 않고 fail-closed 거부하는지 증명한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "I would rather not call a function."}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(GeminiStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_failure_injection_wrong_function_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 변형: 다른 함수 이름의 functionCall(예: 상류 프록시/모델 버전
    혼선)을 절대 우리 스키마의 결과로 오인해 받아들이지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "some_other_tool", "args": {"x": 1}}}
                            ],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(GeminiStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_failure_injection_non_object_args_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: Gemini 계약 고유의 손상 모드 -- `functionCall.args`는 이미
    파싱된 객체여야 하는데, 상류가 배열이나 스칼라를 돌려주는 경우를 시뮬레이션
    한다. JSON 객체가 아니면 예외 없이 조용히 넘어가지 않고 fail-closed
    거부해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "structured_output",
                                        "args": ["not", "an", "object"],
                                    }
                                }
                            ],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(GeminiStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_network_error_raises_upstream_error_with_no_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(GeminiUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code is None


# --- 성능 단언 + 게이트 적색 재현 ---

_OVERHEAD_BUDGET_SECONDS = 0.05


async def test_generate_overhead_p95_under_budget_with_instant_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성능 단언: 네트워크 자체를 즉시 응답으로 고정한 상태에서, 어댑터가 직접
    수행하는 작업(사전 비용 추정·페이로드 직렬화·usageMetadata 파싱·
    비용/해시 계산)의 p95 지연이 예산을 넘지 않아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _function_call_response({"hypothesis": "x"})

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


async def test_gate_red_repro_injected_transport_latency_breaches_the_same_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 성능 단언이 실제로 지연을 감지하는지(항상 통과하는
    타우톨로지가 아닌지) 증명한다. 전송에 예산을 초과하는 지연을 주입하면 같은
    측정 방식이 예산 초과를 실제로 잡아낸다."""
    injected_delay = _OVERHEAD_BUDGET_SECONDS * 3

    def slow_handler(request: httpx.Request) -> httpx.Response:
        time.sleep(injected_delay)
        return _function_call_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(slow_handler))
    provider = _provider()

    start = time.perf_counter()
    await provider.generate(_SCHEMA, _prompt(), _budget())
    elapsed = time.perf_counter() - start

    assert elapsed >= _OVERHEAD_BUDGET_SECONDS, (
        "적색 재현 실패 — 주입한 지연이 측정에 반영되지 않았다(타우톨로지 위험)"
    )
