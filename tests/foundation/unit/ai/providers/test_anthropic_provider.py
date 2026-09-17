"""Contract tests (fake transport) for `src/foundation/ai/providers/adapters/
anthropic_provider.py` -- task-2640 AI-6 ("Claude API(구조화 출력, 비용 계측) +
계약 테스트(fake)").

No real Anthropic API call is made anywhere in this file: every test injects
`httpx.MockTransport` the same way `tests/foundation/unit/risk/
test_telegram_adapter.py` fakes the Telegram Bot API -- monkeypatch the
adapter module's `httpx.AsyncClient` to a factory that pins `transport=`.
`anthropic` (the PyPI SDK) is never imported; this adapter talks to the
documented `/v1/messages` HTTP contract directly (module docstring), so no
package needs to be installed for these tests to exercise the real code path.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.foundation.ai.providers.adapters.anthropic_provider import (
    AnthropicCostCapExceededError,
    AnthropicPricing,
    AnthropicProvider,
    AnthropicStructuredOutputError,
    AnthropicUpstreamError,
)
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, ModelProvider

_SCHEMA = {
    "type": "object",
    "properties": {"hypothesis": {"type": "string"}},
    "required": ["hypothesis"],
}
_PRICING = AnthropicPricing(
    input_cost_per_token=Decimal("0.000003"), output_cost_per_token=Decimal("0.000015")
)


def _prompt(template: str = "propose a mean-reversion strategy") -> PromptTemplate:
    return PromptTemplate(prompt_id="strategy.propose", version=1, template=template)


def _budget(*, cost_cap: str = "1.00", max_output_tokens: int = 1000) -> GenerationBudget:
    return GenerationBudget(cost_cap=Decimal(cost_cap), max_output_tokens=max_output_tokens)


def _provider(**kwargs: Any) -> AnthropicProvider:
    defaults: dict[str, Any] = {
        "api_key": "sk-ant-test",
        "model": "claude-test-model",
        "pricing": _PRICING,
    }
    defaults.update(kwargs)
    return AnthropicProvider(**defaults)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    import src.foundation.ai.providers.adapters.anthropic_provider as adapter_module

    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _factory)


def _tool_use_response(data: dict[str, Any], *, input_tokens: int = 100, output_tokens: int = 50):
    return httpx.Response(
        200,
        json={
            "content": [{"type": "tool_use", "name": "structured_output", "input": data}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


# --- 정상 경로: 구조적 적합성 + 계약 ---


def test_provider_satisfies_model_provider_protocol() -> None:
    assert isinstance(_provider(), ModelProvider)


async def test_generate_returns_structured_output_with_measured_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _tool_use_response({"hypothesis": "buy the dip"}, input_tokens=100, output_tokens=50)

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
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = req.content.decode("utf-8")
    assert '"tool_choice"' in body and '"structured_output"' in body
    assert '"max_tokens":1000' in body.replace(" ", "")


# --- 부정 테스트 (>=3) ---


def test_empty_api_key_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(api_key="")


def test_empty_model_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        _provider(model="")


def test_negative_pricing_rate_rejected_fail_closed() -> None:
    with pytest.raises(ValueError):
        AnthropicPricing(input_cost_per_token=Decimal("-0.01"), output_cost_per_token=Decimal("0"))


async def test_over_cap_estimate_refuses_upstream_call_before_spending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight refusal: a cap far below the cheapest possible request must
    never reach the network -- `captured` staying empty proves no money could
    have been spent, not just that an exception was raised."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _tool_use_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()
    budget = _budget(cost_cap="0.0000001", max_output_tokens=1000)

    with pytest.raises(AnthropicCostCapExceededError):
        await provider.generate(_SCHEMA, _prompt(), budget)

    assert captured == []


async def test_http_4xx_raises_upstream_error_with_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="authentication_error: invalid x-api-key")

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(AnthropicUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code == 401
    assert "invalid x-api-key" in str(exc_info.value)


# --- 실패 주입: 상류 응답이 손상돼도 자유 텍스트로 조용히 대체하지 않는다 ---


async def test_failure_injection_missing_tool_use_block_is_rejected_not_treated_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 모델이 도구 호출을 거부하고 자유 텍스트만 반환하는 손상된
    응답을 주입한다. §1 "free text is never a valid result"가 실제로 지켜지는지
    -- 조용히 텍스트를 감싸서 반환하지 않고 fail-closed 거부하는지 증명한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "I would rather not call a tool."}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(AnthropicStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_failure_injection_wrong_tool_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 변형: 다른 도구 이름의 tool_use(예: 상류 프록시/모델 버전
    혼선)를 절대 우리 스키마의 결과로 오인해 받아들이지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": "some_other_tool", "input": {"x": 1}}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(AnthropicStructuredOutputError):
        await provider.generate(_SCHEMA, _prompt(), _budget())


async def test_network_error_raises_upstream_error_with_no_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_transport(monkeypatch, httpx.MockTransport(handler))
    provider = _provider()

    with pytest.raises(AnthropicUpstreamError) as exc_info:
        await provider.generate(_SCHEMA, _prompt(), _budget())

    assert exc_info.value.status_code is None


# --- 성능 단언 + 게이트 적색 재현 ---

_OVERHEAD_BUDGET_SECONDS = 0.05


async def test_generate_overhead_p95_under_budget_with_instant_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성능 단언: 네트워크 자체를 즉시 응답으로 고정한 상태에서, 어댑터가 직접
    수행하는 작업(사전 비용 추정·페이로드 직렬화·usage 파싱·비용/해시 계산)의
    p95 지연이 예산을 넘지 않아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_use_response({"hypothesis": "x"})

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
        return _tool_use_response({"hypothesis": "x"})

    _mock_transport(monkeypatch, httpx.MockTransport(slow_handler))
    provider = _provider()

    start = time.perf_counter()
    await provider.generate(_SCHEMA, _prompt(), _budget())
    elapsed = time.perf_counter() - start

    assert elapsed >= _OVERHEAD_BUDGET_SECONDS, (
        "적색 재현 실패 — 주입한 지연이 측정에 반영되지 않았다(타우톨로지 위험)"
    )
