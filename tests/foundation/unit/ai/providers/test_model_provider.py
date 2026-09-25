"""Unit tests for `src/foundation/ai/providers/ports/model_provider.py` --
task-2639 AI-5 ("ModelProvider.generate(schema, prompt, budget) ->
StructuredOutput Protocol. 스키마 강제 출력만").

No concrete adapter exists yet (AI-6/7/7b) -- these tests exercise the port
shape itself: a minimal fake proves any provider satisfying the `Protocol`
is structurally interchangeable (공급자 중립), and the value objects reject
malformed construction fail-closed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate
from src.foundation.ai.providers.ports.model_provider import (
    GenerationBudget,
    ModelProvider,
    StructuredOutput,
)


class _FakeProvider:
    """Deliberately does not inherit from `ModelProvider` -- structural
    typing (`Protocol`) is the whole point of §1 "공급자 중립": a class
    satisfies the port by shape alone, with zero coupling to it."""

    async def generate(
        self,
        schema: dict[str, Any],
        prompt: PromptTemplate,
        budget: GenerationBudget,
    ) -> StructuredOutput:
        return StructuredOutput(
            data={"hypothesis": "fake"},
            prompt_hash="0" * 64,
            cost=Decimal("0.01"),
            output_tokens=42,
        )


class _NotAProvider:
    """Missing `generate` entirely -- must fail the structural check."""


# --- 정상 경로: 구조적 적합성 ---


def test_fake_provider_satisfies_model_provider_protocol() -> None:
    assert isinstance(_FakeProvider(), ModelProvider)


def test_class_without_generate_does_not_satisfy_protocol() -> None:
    assert not isinstance(_NotAProvider(), ModelProvider)


@pytest.mark.asyncio
async def test_fake_provider_generate_returns_structured_output() -> None:
    provider = _FakeProvider()
    budget = GenerationBudget(cost_cap=Decimal("1.00"), max_output_tokens=1000)
    prompt = PromptTemplate(prompt_id="strategy.propose", version=1, template="go")
    result = await provider.generate({"type": "object"}, prompt, budget)
    assert result.data == {"hypothesis": "fake"}
    assert result.cost == Decimal("0.01")


# --- 부정 테스트: 값 객체 생성 규칙 (>=3) ---


def test_generation_budget_rejects_negative_cost_cap() -> None:
    with pytest.raises(ValueError):
        GenerationBudget(cost_cap=Decimal("-0.01"), max_output_tokens=100)


def test_generation_budget_rejects_non_positive_max_output_tokens() -> None:
    with pytest.raises(ValueError):
        GenerationBudget(cost_cap=Decimal("1.00"), max_output_tokens=0)


def test_structured_output_rejects_negative_cost() -> None:
    with pytest.raises(ValueError):
        StructuredOutput(
            data={},
            prompt_hash="0" * 64,
            cost=Decimal("-0.01"),
            output_tokens=1,
        )


def test_structured_output_rejects_negative_output_tokens() -> None:
    with pytest.raises(ValueError):
        StructuredOutput(
            data={},
            prompt_hash="0" * 64,
            cost=Decimal("0"),
            output_tokens=-1,
        )


# --- 실패 주입: 상류 손상이 조용히 통과하지 않고 fail-closed 거부 ---


def test_generation_budget_rejects_string_contaminated_cost_cap_from_upstream_config() -> None:
    """실패 주입: 상류(설정 로더)가 손상되어 `cost_cap`이 Decimal이 아니라
    문자열로 섞여 들어오면, 문자열과 int의 순서 비교는 TypeError를 내므로
    "음수 아님"으로 조용히 통과시키지 않고 fail-closed 거부해야 한다."""
    with pytest.raises(TypeError):
        GenerationBudget(cost_cap="1.00", max_output_tokens=100)
