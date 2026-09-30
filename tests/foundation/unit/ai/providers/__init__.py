"""tests/foundation/unit/ai/providers/__init__.py -- ai/providers 패키지
cross-module 부정/실패주입/성능 테스트 (domain/budget.py -> ports/model_provider.py
-> adapters/anthropic_provider.py -> domain/prompt_registry.py 경계를 가로지르는
계약).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 -- negative>=3, failure-injection>=1,
perf assertion>=1.

개별 모듈 단위 테스트는 test_budget.py/test_model_provider.py/
test_anthropic_provider.py/test_prompt_registry.py에 이미 두껍게 있다 -- 여기서는
그 모듈 경계를 넘나드는, 개별 파일 테스트로는 드러나지 않는 계약에 집중한다:

- `domain/budget.py::TenantBudget.reserve()`가 승인한 잔여 예산이
  `ports/model_provider.py::GenerationBudget.cost_cap`으로 넘어갈 때, 두 계층이
  독립적으로(fail-closed, 이중 방어) 음수/한도초과를 거부하는지.
- `adapters/anthropic_provider.py::AnthropicProvider.generate()`가
  `GenerationBudget.cost_cap`을 넘는 사전추정치(pre-flight estimate)에 대해
  실제 업스트림 HTTP 호출 전에 거부하는지 -- "예산 승인 -> 실제 호출" 경로가
  모듈 경계를 넘어도 끊기지 않는지.
- `domain/prompt_registry.py::prompt_hash()`가 `AnthropicProvider.generate()`의
  반환값(`StructuredOutput.prompt_hash`)에 그대로 재현 가능한 형태로 실려
  나오는지(재현성 키 성분 보존).
"""

from __future__ import annotations

import statistics
import time
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest

from src.foundation.ai.providers.adapters.anthropic_provider import (
    AnthropicCostCapExceededError,
    AnthropicPricing,
    AnthropicProvider,
    AnthropicUpstreamError,
)
from src.foundation.ai.providers.domain.budget import (
    BudgetExceededError,
    BudgetRuleError,
    TenantBudget,
    reserve,
)
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate, prompt_hash
from src.foundation.ai.providers.ports.model_provider import GenerationBudget

_SCHEMA: dict[str, object] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _tenant_budget(**overrides: Any) -> TenantBudget:
    base: dict[str, Any] = dict(
        tenant_id=uuid4(),
        token_id=uuid4(),
        daily_cap=Decimal("10.00"),
        spent_today=Decimal("0.00"),
        period_start=date(2026, 1, 1),
    )
    base.update(overrides)
    return TenantBudget(**base)


def _prompt() -> PromptTemplate:
    return PromptTemplate(prompt_id="ai-provider-cross-module", version=1, template="hello")


def _provider(*, pricing: AnthropicPricing | None = None) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test-key",
        model="claude-test",
        pricing=pricing
        or AnthropicPricing(
            input_cost_per_token=Decimal("1"),
            output_cost_per_token=Decimal("1"),
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests -- 모듈 경계를 넘는 불변식 위반 입력 거부
# ──────────────────────────────────────────────────────────────────────


def test_negative_reserve_denies_cost_that_would_fund_an_over_budget_generation() -> None:
    """부정: `TenantBudget.reserve()`가 남은 예산을 넘는 `cost`를 거부하면,
    그 값은 `GenerationBudget.cost_cap`으로 절대 넘어가지 못한다 -- 예산 계층이
    먼저 막아서 provider 포트까지 승인되지 않은 cap이 도달하지 않는다."""
    budget = _tenant_budget(daily_cap=Decimal("5.00"), spent_today=Decimal("4.00"))
    with pytest.raises(BudgetExceededError):
        reserve(budget, Decimal("2.00"))  # remaining=1.00 < requested=2.00


def test_negative_generation_budget_rejects_negative_cost_cap_even_after_tenant_budget_ok() -> None:
    """부정: `TenantBudget.reserve()`가 통과시킨 잔여값이라도, 호출자가 부호를
    뒤집어(`-`) `GenerationBudget.cost_cap`에 넘기면 포트 계층이 독립적으로
    다시 거부한다 -- 상위 계층 승인이 하위 계층 검증을 건너뛰는 지름길이
    아니다(2중 방어)."""
    tenant_budget = reserve(_tenant_budget(), Decimal("3.00"))
    approved_remaining = Decimal("10.00") - tenant_budget.spent_today
    assert approved_remaining > 0
    with pytest.raises(ValueError):
        GenerationBudget(cost_cap=-approved_remaining, max_output_tokens=100)


def test_negative_tenant_budget_rejects_negative_spent_today_before_any_reserve_call() -> None:
    """부정: 애초에 손상된(`spent_today < 0`) `TenantBudget`은 `reserve()`
    호출 전 생성 시점에 이미 거부된다 -- provider 포트까지 도달할 손상된 상태를
    만들 수 없다."""
    with pytest.raises(BudgetRuleError):
        _tenant_budget(spent_today=Decimal("-0.01"))


async def test_negative_anthropic_provider_refuses_call_when_estimate_exceeds_cost_cap() -> None:
    """부정: 사전추정치가 `GenerationBudget.cost_cap`을 넘으면 `AnthropicProvider`
    는 실제 HTTP 호출을 하지 않고 거부한다 -- 예산 승인 경로가 provider
    adapter 안에서도 끊기지 않고 재검증된다."""
    provider = _provider(
        pricing=AnthropicPricing(
            input_cost_per_token=Decimal("1000"),
            output_cost_per_token=Decimal("1000"),
        )
    )
    tiny_budget = GenerationBudget(cost_cap=Decimal("0.01"), max_output_tokens=1)
    with pytest.raises(AnthropicCostCapExceededError):
        await provider.generate(_SCHEMA, _prompt(), tiny_budget)


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests -- 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


async def test_failure_injection_anthropic_provider_propagates_transport_error_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: 업스트림 HTTP 호출이 예상 밖 전송 예외(`httpx.ConnectError`)를
    던지면, 예산/프롬프트 계층을 이미 통과한 이후에도 `AnthropicUpstreamError`로
    감싸져 그대로 전파돼야 한다 -- 조용히 삼켜서 빈 `StructuredOutput`을
    반환하면 안 된다(fail-closed)."""

    class _BoomClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _BoomClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> None:
            raise httpx.ConnectError("injected transport failure")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)

    provider = _provider()
    generous_budget = GenerationBudget(cost_cap=Decimal("1000000"), max_output_tokens=100)
    with pytest.raises(AnthropicUpstreamError):
        await provider.generate(_SCHEMA, _prompt(), generous_budget)


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_perf_budget_reserve_plus_prompt_hash_pipeline() -> None:
    """성능 단언: `reserve()`(예산 판정) + `prompt_hash()`(재현성 키 성분) 조합
    파이프라인의 p95가 ADR-2026-09-09-C 축별 예산(사전거래 게이트 p99 5ms) 안에
    들어와야 한다 -- 둘 다 순수 함수이므로 사전거래 게이트와 같은 레이턴시
    계급(호출당 수 ms 이하)을 기대할 수 있다."""
    template = _prompt()
    samples: list[float] = []
    for _ in range(200):
        budget = _tenant_budget()
        start = time.perf_counter()
        reserved = reserve(budget, Decimal("1.00"))
        prompt_hash(template)
        samples.append(time.perf_counter() - start)
        assert reserved.spent_today == Decimal("1.00")

    p95 = statistics.quantiles(samples, n=20)[18]
    assert p95 < 0.005  # ADR-2026-09-09-C 축별 예산: 사전거래 게이트 p99 5ms


# ──────────────────────────────────────────────────────────────────────
# 4. Cross-module integration tests
# ──────────────────────────────────────────────────────────────────────


async def test_integration_reserved_budget_funds_generation_and_prompt_hash_is_reproducible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """통합: `TenantBudget.reserve()`가 승인한 잔여값으로 만든
    `GenerationBudget`이 `AnthropicProvider.generate()`를 정상적으로 통과하고,
    반환된 `StructuredOutput.prompt_hash`가 `prompt_hash()`를 직접 호출한
    값과 동일하다(재현성 키 성분이 모듈 경계를 넘어도 그대로 보존)."""

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "content": [
                    {"type": "tool_use", "name": "structured_output", "input": {"ok": True}}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    tenant_budget = _tenant_budget(daily_cap=Decimal("10.00"))
    reserved = reserve(tenant_budget, Decimal("1.00"))
    remaining_after = Decimal("10.00") - reserved.spent_today
    generation_budget = GenerationBudget(cost_cap=remaining_after, max_output_tokens=50)

    template = _prompt()
    cheap_provider = _provider(
        pricing=AnthropicPricing(
            input_cost_per_token=Decimal("0.0001"),
            output_cost_per_token=Decimal("0.0001"),
        )
    )
    output = await cheap_provider.generate(_SCHEMA, template, generation_budget)

    assert output.prompt_hash == prompt_hash(template)
    assert output.data == {"ok": True}


def test_integration_duplicate_prompt_registration_rejected_regardless_of_budget_state() -> None:
    """통합: 예산 상태(승인/거부)와 무관하게 `PromptRegistry`의 `(prompt_id,
    version)` 유일성 불변식은 독립적으로 유지된다 -- 예산 계층과 프롬프트
    계층은 서로의 검증을 대체하지 않는, 완전히 독립된 두 축이다."""
    from src.foundation.ai.providers.domain.prompt_registry import (
        DuplicatePromptVersionError,
        PromptRegistry,
    )

    registry = PromptRegistry.empty().register(_prompt())

    with pytest.raises(BudgetExceededError):
        reserve(_tenant_budget(daily_cap=Decimal("1.00")), Decimal("2.00"))

    with pytest.raises(DuplicatePromptVersionError):
        registry.register(_prompt())
