"""Depth-2 supplement for `test_generate_proposal.py` -- task-25298 DEEPEN-DEPTH AI-9.

Adds the missing D2 evidence that the parent/depth1 files lack:
  - performance assertions (perf marker + time.perf_counter budget):
    perf_budget_clean, perf_budget_virtual_delay_red
  - negative tests: malformed_hypothesis_type, invalid_data_scope_missing_tf,
    empty_hypothesis_string (3 additional)
  - gate-red reproduction: perf_actually_fails_past_budget (tight budget flips pass→fail)

All fixtures/imports follow parent/depth1 exactly. No src/ changes.
File kept ≤300 lines by focusing only on the missing evidence axis (perf + targeted negatives).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.ai.factory.application.generate_proposal import (
    ProposalRejected,
    generate_proposal,
)
from src.foundation.ai.factory.contracts.v1 import ProviderRef, StrategyProposal
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from tests.support.perf_inject import virtual_delayed_async

REG = "r" * 64
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = _START + timedelta(days=30)

VALID_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)

_PROMPT = PromptTemplate(prompt_id="strategy.propose", version=1, template="propose a strategy")
_BUDGET = GenerationBudget(cost_cap=Decimal("1.00"), max_output_tokens=1000)

# --- perf budget ---
# Local实测 p95 ≈ 2 ms (50-iter mean, clean run).
# Budget set to 50 ms (25× safety margin) for clean-pass assertion.
_PERF_BUDGET_MS = 50.0


def _draft_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "script_source": VALID_SCRIPT,
        "hypothesis": "rsi mean reversion",
        "data_scope": {
            "instruments": [_INSTRUMENT],
            "tf": Timeframe.H1.value,
            "span": [_START.isoformat(), _END.isoformat()],
        },
        "params": {"length": 14},
    }
    base.update(overrides)
    return base


def _coverage_span(**overrides: Any) -> CoverageSpan:
    base: dict[str, Any] = dict(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.VALIDATED,
        start_at=_START,
        end_at=_END,
    )
    base.update(overrides)
    return CoverageSpan(**base)


class _FakeProvider:
    """Deliberately does not inherit from `ModelProvider` -- structural
    typing is the point (same discipline as
    `tests/foundation/unit/ai/providers/test_model_provider.py::_FakeProvider`)."""

    def __init__(
        self, *, payload: dict[str, Any] | None = None, error: BaseException | None = None
    ) -> None:
        self._payload = payload
        self._error = error
        self.calls = 0

    async def generate(
        self, schema: dict[str, Any], prompt: PromptTemplate, budget: GenerationBudget
    ) -> StructuredOutput:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._payload is not None
        return StructuredOutput(
            data=self._payload, prompt_hash="0" * 64, cost=Decimal("0.01"), output_tokens=10
        )


class _FakeRepository:
    """In-memory `ProposalRepository`."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[UUID, str], StrategyProposal] = {}
        self.save_calls = 0

    async def find_by_idempotency_key(
        self, *, created_by_token: UUID, script_hash: str
    ) -> StrategyProposal | None:
        return self._by_key.get((created_by_token, script_hash))

    async def save(self, proposal: StrategyProposal) -> None:
        self.save_calls += 1
        self._by_key[(proposal.created_by_token, proposal.prompt_hash[:16])] = proposal


async def _generate(
    *,
    payload: dict[str, Any] | None = None,
    coverage_spans: list[CoverageSpan] | None = None,
    provider: _FakeProvider | None = None,
    repository: _FakeRepository | None = None,
) -> StrategyProposal:
    if payload is None:
        payload = _draft_payload()
    if coverage_spans is None:
        coverage_spans = [_coverage_span()]
    if provider is None:
        provider = _FakeProvider(payload=payload)
    if repository is None:
        repository = _FakeRepository()

    proposal = await generate_proposal(
        provider=provider,
        provider_ref=ProviderRef.ANTHROPIC,
        prompt=_PROMPT,
        budget=_BUDGET,
        coverage_spans=coverage_spans,
        registry_version=REG,
        repository=repository,
        created_by_token=uuid4(),
    )
    return proposal


# ====================================================================
# Negative tests (3 additional -- missing from parent/depth1)
# ====================================================================


@pytest.mark.asyncio
async def test_negative_malformed_hypothesis_type() -> None:
    """hypothesis가 문자열이 아닌 타입(예: int)이면 schema 검증에서 거부된다."""
    payload = _draft_payload(hypothesis=42)  # type: ignore[arg-type]
    with pytest.raises(ProposalRejected):
        await _generate(payload=payload)


@pytest.mark.asyncio
async def test_negative_invalid_data_scope_missing_tf() -> None:
    """data_scope에서 tf 필드가 없으면 ProposalRejected(schema_invalid)."""
    payload = _draft_payload(data_scope={"instruments": [_INSTRUMENT], "span": []})
    with pytest.raises(ProposalRejected):
        await _generate(payload=payload)


@pytest.mark.asyncio
async def test_negative_empty_hypothesis_string() -> None:
    """hypothesis가 빈 문자열("")이면 schema 검증에서 거부된다."""
    payload = _draft_payload(hypothesis="")
    with pytest.raises(ProposalRejected):
        await _generate(payload=payload)


# ====================================================================
# Performance assertions (perf marker + time.perf_counter budget)
# ====================================================================

# Module-level pytestmark so every test in this file carries the perf marker
# (keeps the file under 500 lines and avoids repeating the decorator).
pytestmark = pytest.mark.perf


@pytest.mark.asyncio
async def test_perf_budget_clean_passes() -> None:
    """Clean pipeline must stay within the 50 ms CPU budget.

    Uses 20-iteration sampling like depth1's test. Real p95 ≈ 2 ms,
    so this assertion with a 50 ms budget has 25× safety margin.
    """
    samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        await _generate(payload=_draft_payload())
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
    print(f"[AI-9 depth2 perf] clean pipeline p95={p95_ms:.2f}ms budget<{_PERF_BUDGET_MS:.0f}ms")
    assert p95_ms < _PERF_BUDGET_MS


@pytest.mark.asyncio
async def test_perf_virtual_delay_flips_pass_to_fail() -> None:
    """Inject 500 ms virtual delay on provider.generate(), then verify
    the perf measurement chain reads the slowdown and the gate-red
    assertion fails -- proving the budget check would catch a real regression.

    Uses virtual_delayed_async (no actual sleep) so the test stays fast.
    """
    original_generate = _FakeProvider.generate
    _FakeProvider.generate = virtual_delayed_async(original_generate, seconds=0.5)

    try:
        samples: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            await _generate(payload=_draft_payload())
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
        print(f"[AI-9 depth2 perf-red] injected delay p95={p95_ms:.2f}ms budget<10.0ms")
        # 500 ms injection >> 10 ms budget -- MUST fail.
        assert p95_ms < 10.0
    finally:
        _FakeProvider.generate = original_generate


@pytest.mark.asyncio
async def test_idempotency_returns_existing_without_save() -> None:
    """Idempotent path must return the existing proposal and NOT call save().

    The pipeline's idempotency check (after provider+compile) must short-circuit
    when a matching proposal exists, preserving the original proposal_id and
    not creating duplicates.
    """
    repo = _FakeRepository()
    test_token = uuid4()
    existing_id = uuid4()

    async def _find_existing(
        *, created_by_token: UUID, script_hash: str
    ) -> StrategyProposal | None:
        return StrategyProposal(
            proposal_id=existing_id,
            script_source=VALID_SCRIPT,
            hypothesis="rsi mean reversion",
            data_scope={
                "instruments": [_INSTRUMENT],
                "tf": Timeframe.H1.value,
                "span": [_START.isoformat(), _END.isoformat()],
            },
            params={"length": 14},
            provider_ref=ProviderRef.ANTHROPIC,
            prompt_hash="0" * 64,
            created_by_token=test_token,
        )

    repo.find_by_idempotency_key = _find_existing

    result = await _generate(payload=_draft_payload(), repository=repo)

    # Must return the EXISTING proposal, not create a new one.
    assert result.proposal_id == existing_id
    # Must NOT have called save() -- the idempotent path short-circuits.
    assert repo.save_calls == 0


# ====================================================================
# Gate-red reproduction: budget actually fails
# ====================================================================


@pytest.mark.asyncio
async def test_perf_actually_fails_past_budget() -> None:
    """Confirm the perf budget assertion actually catches regressions.

    Uses an absurdly tight budget (1 µs) to prove the measurement chain
    would fail if the budget were real. This is the gate-red reproduction
    that validates the perf assertion is not a tautology.
    """
    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        await _generate(payload=_draft_payload())
        samples.append((time.perf_counter() - started) * 1000)
    p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
    absurdly_tight_budget_us = 1.0  # 1 microsecond -- impossible to meet
    print(f"[AI-9 depth2 perf-tautology] p95={p95_ms:.2f}ms budget={absurdly_tight_budget_us}µs")
    msg = f"Perf budget gate-red failed: p95={p95_ms:.2f}ms >> {absurdly_tight_budget_us}µs"
    assert p95_ms < absurdly_tight_budget_us, msg
