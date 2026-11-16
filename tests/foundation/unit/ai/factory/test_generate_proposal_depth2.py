"""Depth-2 supplement for `test_generate_proposal.py` -- task-25298 DEEPEN-DEPTH AI-9.

Adds D2 evidence the parent and depth1 files lack: three new negative tests,
a failure-injection test (no persistence after a provider crash), and a
`perf_budget.best_of_async` budget with a 10x virtual-delay gate-red reproduction.
Fixtures/imports follow `test_generate_proposal_depth1.py`. No src/ changes.
"""

from __future__ import annotations

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
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

REG = "r" * 64
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = _START + timedelta(days=30)
_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
_PROMPT = PromptTemplate(prompt_id="strategy.propose", version=1, template="propose a strategy")
_BUDGET = GenerationBudget(cost_cap=Decimal("1.00"), max_output_tokens=1000)

# Budget: best-of-5 CPU of one fake-provider generate_proposal call (compile +
# schema + coverage).  Measured locally: cpu_ms reads 0.00 (below the Windows
# ~15.6 ms process_time tick, batch=4); wall of one call is a few ms.  300 ms is
# far above any plausible measurement (>=30x the tick-bounded reading).
_BUDGET_MS = 300.0
_INJECT_SECONDS = 3.0  # virtual wall delay, >=10x the budget


def _span() -> CoverageSpan:
    return CoverageSpan(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.VALIDATED,
        start_at=_START,
        end_at=_END,
    )


class _FakeProvider:
    def __init__(
        self, *, payload: dict[str, Any] | None = None, error: BaseException | None = None
    ) -> None:
        self._payload = payload
        self._error = error

    async def generate(
        self, schema: dict[str, Any], prompt: PromptTemplate, budget: GenerationBudget
    ) -> StructuredOutput:
        if self._error is not None:
            raise self._error
        assert self._payload is not None
        return StructuredOutput(
            data=self._payload, prompt_hash="0" * 64, cost=Decimal("0.01"), output_tokens=10
        )


class _FakeRepository:
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


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "script_source": _SCRIPT,
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


async def _generate(
    *,
    payload: dict[str, Any] | None = None,
    error: BaseException | None = None,
    repo: _FakeRepository | None = None,
) -> StrategyProposal:
    return await generate_proposal(
        provider=_FakeProvider(payload=payload, error=error),
        provider_ref=ProviderRef.ANTHROPIC,
        prompt=_PROMPT,
        budget=_BUDGET,
        coverage_spans=[_span()],
        registry_version=REG,
        repository=repo if repo is not None else _FakeRepository(),
        created_by_token=uuid4(),
    )


@pytest.mark.asyncio
async def test_negative_script_source_wrong_type_rejected() -> None:
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_payload(script_source=12345))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_data_scope_not_object_rejected() -> None:
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_payload(data_scope="BTCUSDT"))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"


@pytest.mark.asyncio
async def test_negative_unknown_timeframe_rejected() -> None:
    scope = {"instruments": [_INSTRUMENT], "tf": "7y", "span": [_START.isoformat()]}
    with pytest.raises(ProposalRejected):
        await _generate(payload=_payload(data_scope=scope))


@pytest.mark.asyncio
async def test_failure_injection_provider_crash_persists_nothing() -> None:
    repo = _FakeRepository()
    with pytest.raises(ProviderUnavailable) as exc_info:
        await _generate(payload=_payload(), error=RuntimeError("boom"), repo=repo)
    assert exc_info.value.http_status == 503
    assert repo.save_calls == 0


@pytest.mark.perf
@pytest.mark.asyncio
async def test_perf_generate_proposal_best_of_within_budget(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    sample = await perf_budget.best_of_async(
        lambda: _generate(payload=_payload()), n=5, warmup=1, batch=4
    )
    print(f"[AI-9 depth2 perf] best_of cpu={sample.cpu_ms:.2f}ms budget<{_BUDGET_MS:.0f}ms")
    assert sample.cpu_ms < _BUDGET_MS


@pytest.mark.perf
@pytest.mark.asyncio
async def test_perf_gate_red_virtual_delay_10x_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red: 10x virtual delay on provider.generate must break the budget."""
    reset_virtual_totals()
    monkeypatch.setattr(
        _FakeProvider, "generate", virtual_delayed_async(_FakeProvider.generate, _INJECT_SECONDS)
    )
    sample = await perf_budget.best_of_async(lambda: _generate(payload=_payload()), n=3, warmup=0)
    reset_virtual_totals()
    assert sample.wall_ms >= _INJECT_SECONDS * 1000
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _BUDGET_MS
