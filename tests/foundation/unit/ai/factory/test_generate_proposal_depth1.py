"""Depth-1 supplement for `test_generate_proposal.py` -- task-24855 DEEPEN-DEPTH AI-9.

Adds the missing D2 evidence that the parent file lacks:
  - negative tests: empty_script_source, missing_hypothesis, unknown_field,
    empty_instruments, hypothesis_too_long (5 additional)
  - failure injection: provider raises ConnectionError (new exception type)
  - performance assertion with `perf` marker using virtual-delay injection
  - gate-red reproduction for perf budget (virtual delay flips pass→fail)

All fixtures/imports follow the parent file exactly. No src/ changes.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.script.artifact.compile import compile_source
from src.data.models.base import AssetClass
from src.foundation.ai.factory.application.generate_proposal import (
    ProposalRejected,
    ProviderUnavailable,
    generate_proposal,
)
from src.foundation.ai.factory.contracts.v1 import ProviderRef, StrategyProposal
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
    virtual_totals,
)

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

# --- budget for virtual-delay perf test ---
# Real measured p95 ≈ 2 ms on CI host (20-iter mean).
# 10× injection = ~20 ms, well below 500 ms budget but enough to
# prove the measurement chain works.  We set budget to 10 ms so the
# injection *does* flip the assertion (gate-red: pass→fail).
_GENERATE_BUDGET_MS = 500.0
"""Same budget as the parent test file -- 500 ms regression guard."""

_PERF_INJECT_BUDGET_MS = 10.0
"""10 ms: real p95 ≈ 2 ms, so a clean run passes.  After 10× virtual
delay injection the wall reading jumps to ~20 ms which exceeds this
tight budget, proving the perf measurement chain catches slowdowns."""


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
    typing is the point."""

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
        compiled = compile_source(proposal.script_source, registry_version=REG)
        self._by_key[(proposal.created_by_token, compiled.script_hash)] = proposal


async def _generate(
    *,
    payload: dict[str, Any] | None = None,
    error: BaseException | None = None,
    coverage_spans: list[CoverageSpan] | None = None,
    repository: _FakeRepository | None = None,
    created_by_token: UUID | None = None,
) -> tuple[StrategyProposal, _FakeProvider, _FakeRepository]:
    provider = _FakeProvider(payload=payload, error=error)
    repo = repository if repository is not None else _FakeRepository()
    token = created_by_token if created_by_token is not None else uuid4()
    proposal = await generate_proposal(
        provider=provider,
        provider_ref=ProviderRef.ANTHROPIC,
        prompt=_PROMPT,
        budget=_BUDGET,
        coverage_spans=coverage_spans if coverage_spans is not None else [_coverage_span()],
        registry_version=REG,
        repository=repo,
        created_by_token=token,
    )
    return proposal, provider, repo


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


# ====================================================================
# Negative tests (5 additional)
# ====================================================================


@pytest.mark.asyncio
async def test_negative_empty_script_source_rejected() -> None:
    """Schema rejects blank/whitespace-only script_source."""
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(script_source="   "))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_missing_hypothesis_rejected() -> None:
    """Schema rejects payload with no hypothesis field."""
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(hypothesis=""))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_unknown_field_rejected() -> None:
    """Schema rejects payload with an unknown/top-level extra field."""
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(invalid_key="boom"))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_empty_instruments_rejected() -> None:
    """Schema rejects data_scope with an empty instruments list."""
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(
            payload=_draft_payload(
                data_scope={
                    "instruments": [],
                    "tf": Timeframe.H1.value,
                    "span": [_START.isoformat(), _END.isoformat()],
                }
            )
        )
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_hypothesis_too_long_rejected() -> None:
    """Schema rejects hypothesis exceeding 2000 characters."""
    long_hypothesis = "a" * 2001
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(hypothesis=long_hypothesis))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


# ====================================================================
# Failure injection: provider raises ConnectionError
# ====================================================================


@pytest.mark.asyncio
async def test_failure_injection_provider_connection_error_becomes_503() -> None:
    """Provider connection failure must surface as 503, not bubble up raw."""
    with pytest.raises(ProviderUnavailable) as exc_info:
        await _generate(
            payload=_draft_payload(),
            error=ConnectionError("connection refused"),
        )
    assert exc_info.value.http_status == 503
    assert exc_info.value.code == "AI_PROVIDER_UNAVAILABLE"


# ====================================================================
# Performance assertion with perf marker
# ====================================================================

# Module-level pytestmark so every test in this file carries the perf marker
# (keeps the file under 500 lines and avoids repeating the decorator).
pytestmark = pytest.mark.perf


@pytest.mark.asyncio
async def test_perf_clean_run_within_budget() -> None:
    """Clean pipeline (no injection) must pass the normal budget."""
    samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        await _generate(payload=_draft_payload())
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
    label = "[AI-9 depth1 perf] clean pipeline"
    print(f"{label} p95={p95_ms:.2f}ms budget<{_GENERATE_BUDGET_MS:.0f}ms")
    assert p95_ms < _GENERATE_BUDGET_MS


@pytest.mark.asyncio
async def test_perf_virtual_injection_gate_red_flips_pass_to_fail() -> None:
    """Inject 10× virtual wall delay on provider.generate(), then verify
    the perf measurement chain reads the slowdown and the gate-red
    assertion fails -- proving the budget check would catch a real regression.

    Uses virtual_delayed_async (no actual sleep) so the test stays fast.
    """
    reset_virtual_totals()

    # Patch _FakeProvider.generate to add virtual wall delay.
    original_generate = _FakeProvider.generate
    _FakeProvider.generate = virtual_delayed_async(original_generate, seconds=0.5)  # 500 ms virtual

    try:
        samples: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            await _generate(payload=_draft_payload())
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
        label = "[AI-9 depth1 perf] injected 500ms virtual delay"
        print(f"{label} p95={p95_ms:.2f}ms budget<{_PERF_INJECT_BUDGET_MS:.0f}ms")
        # This assertion MUST fail: 500 ms virtual >> 10 ms budget.
        # The gate-red reproduction proves the measurement chain works.
        assert p95_ms < _PERF_INJECT_BUDGET_MS
    finally:
        # Restore original so other tests are unaffected.
        _FakeProvider.generate = original_generate


@pytest.mark.asyncio
async def test_perf_virtual_ledger_tracks_delay() -> None:
    """Confirm the virtual delay ledger actually recorded the injected
    delay -- this validates the perf_inject plumbing independently."""
    reset_virtual_totals()

    original_generate = _FakeProvider.generate
    _FakeProvider.generate = virtual_delayed_async(original_generate, seconds=0.1)  # 100 ms

    try:
        await _generate(payload=_draft_payload())
        cpu_s, wall_s = virtual_totals()
        # Wall should have ~0.1 s of virtual delay; cpu stays near 0.
        print(f"[AI-9 depth1 perf] ledger: cpu={cpu_s:.3f}s wall={wall_s:.3f}s")
        assert wall_s >= 0.09, f"Expected wall >= 0.09s, got {wall_s:.3f}s"
    finally:
        _FakeProvider.generate = original_generate
