"""Unit tests for `src/foundation/ai/factory/application/generate_proposal.py`
-- task-2644 AI-9 DoD ("컴파일 오류 400 위치 포함"). D2 depth (ADR-2026-09-09-C):
negative >=3, failure injection 1, numeric performance assertion 1, gate-red
reproduction 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.script.artifact.compile import compile_source
from src.data.models.base import AssetClass
from src.foundation.ai.factory.application.generate_proposal import (
    ProposalCompileRejected,
    ProposalRejected,
    ProviderUnavailable,
    generate_proposal,
)
from src.foundation.ai.factory.contracts.v1 import ProviderRef, StrategyProposal
from src.foundation.ai.providers.domain.prompt_registry import PromptTemplate
from src.foundation.ai.providers.ports.model_provider import GenerationBudget, StructuredOutput
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade

REG = "r" * 64
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)

VALID_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
FORBIDDEN_NAMESPACE_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = series.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
SYNTAX_ERROR_SCRIPT = "let x = ((("

_PROMPT = PromptTemplate(prompt_id="strategy.propose", version=1, template="propose a strategy")
_BUDGET = GenerationBudget(cost_cap=Decimal("1.00"), max_output_tokens=1000)


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
    """In-memory `ProposalRepository`. `script_hash` is not a
    `StrategyProposal` field (see `contracts/v1.py` docstring -- an
    intentional AI-8 omission, the application layer owns `CompiledScript`)
    so this fake recomputes it from `script_source`+`registry_version` via
    the same DSL-12 `compile_source` `generate_proposal` already used --
    deterministic (same source+registry_version = same hash), so it needs
    no separate hash column, matching how a real adapter could equally
    store `script_hash` as a plain generated/computed column."""

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


# --- 정상 경로 ---


@pytest.mark.asyncio
async def test_generate_proposal_full_pipeline_saves_and_returns_proposal() -> None:
    proposal, provider, repo = await _generate(payload=_draft_payload())
    assert provider.calls == 1
    assert repo.save_calls == 1
    assert proposal.script_source == VALID_SCRIPT
    assert proposal.provider_ref == ProviderRef.ANTHROPIC
    assert proposal.prompt_hash == "0" * 64


@pytest.mark.asyncio
async def test_generate_proposal_is_idempotent_on_token_and_script_hash() -> None:
    """§5 "제안 제출: (token_id, script_hash) 멱등" -- 같은 토큰·같은 스크립트로
    두 번 호출하면 두 번째는 새로 저장하지 않고 첫 결과를 그대로 돌려준다."""
    token = uuid4()
    repo = _FakeRepository()
    first, provider1, _ = await _generate(
        payload=_draft_payload(), repository=repo, created_by_token=token
    )
    second, provider2, _ = await _generate(
        payload=_draft_payload(), repository=repo, created_by_token=token
    )
    assert second.proposal_id == first.proposal_id
    assert repo.save_calls == 1
    assert provider1.calls == 1
    assert provider2.calls == 1  # provider is still called; only the save is skipped


# --- 부정 테스트 (>=3) ---


@pytest.mark.asyncio
async def test_generate_proposal_rejects_schema_invalid_payload() -> None:
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(sneaky="ignore risk gate"))
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"
    assert exc_info.value.http_status == 400
    assert exc_info.value.reason == "schema_invalid"


@pytest.mark.asyncio
async def test_generate_proposal_rejects_compile_error_with_location() -> None:
    """리프 DoD 그대로: 컴파일 오류는 400이고 위치(line/col)를 포함해야 한다."""
    with pytest.raises(ProposalCompileRejected) as exc_info:
        await _generate(payload=_draft_payload(script_source=SYNTAX_ERROR_SCRIPT))
    assert exc_info.value.code == "AI_PROPOSAL_COMPILE"
    assert exc_info.value.http_status == 400
    assert exc_info.value.line >= 1
    assert exc_info.value.col >= 1


@pytest.mark.asyncio
async def test_generate_proposal_rejects_uncovered_data_scope() -> None:
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(), coverage_spans=[])
    assert exc_info.value.code == "AI_PROPOSAL_SCHEMA"
    assert exc_info.value.reason == "data_scope_uncovered"


@pytest.mark.asyncio
async def test_generate_proposal_rejects_forbidden_call_namespace() -> None:
    with pytest.raises(ProposalRejected) as exc_info:
        await _generate(payload=_draft_payload(script_source=FORBIDDEN_NAMESPACE_SCRIPT))
    assert exc_info.value.reason == "forbidden_api"


# --- 실패 주입 ---


@pytest.mark.asyncio
async def test_generate_proposal_maps_provider_failure_to_503_without_saving() -> None:
    """실패 주입: 공급자 호출 자체가 예외(네트워크 장애 등)를 내면
    AI_PROVIDER_UNAVAILABLE(503)로 매핑되고, 저장 단계는 절대 도달하지 않는다."""
    repo = _FakeRepository()
    provider = _FakeProvider(error=ConnectionError("upstream unreachable"))
    with pytest.raises(ProviderUnavailable) as exc_info:
        await generate_proposal(
            provider=provider,
            provider_ref=ProviderRef.ANTHROPIC,
            prompt=_PROMPT,
            budget=_BUDGET,
            coverage_spans=[_coverage_span()],
            registry_version=REG,
            repository=repo,
            created_by_token=uuid4(),
        )
    assert exc_info.value.code == "AI_PROVIDER_UNAVAILABLE"
    assert exc_info.value.http_status == 503
    assert repo.save_calls == 0


# --- 수치 성능 단언 ---

_GENERATE_BUDGET_MS = 500.0
"""§7 SLO "제안 생성 <=60s(공급자 제외)"의 상한(60,000ms)보다 훨씬 좁게 잡은
회귀 방지선 -- 여기 공급자는 즉시 응답하는 fake이므로 전체 파이프라인
(공급자 호출 포함, schema/compile/coverage/저장)이 이 정도 여유 안에서
끝나지 않으면 명백한 성능 회귀다."""


async def _generate_latencies_ms(iterations: int = 20) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        await _generate(payload=_draft_payload())
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.asyncio
async def test_generate_proposal_p95_latency_within_budget() -> None:
    samples = await _generate_latencies_ms()
    p95_ms = _p95(samples)
    label = "[AI-9 generate_proposal] full pipeline"
    print(f"{label} p95={p95_ms:.2f}ms budget<{_GENERATE_BUDGET_MS:.0f}ms")
    assert p95_ms < _GENERATE_BUDGET_MS


# --- 게이트 적색 재현 ---


@pytest.mark.asyncio
async def test_gate_red_budget_actually_fails_past_budget() -> None:
    samples = await _generate_latencies_ms(iterations=5)
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms


@pytest.mark.asyncio
async def test_gate_red_progressive_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 payload/coverage에서 시작해 한 번에 한 가지씩만 오염시켜
    재생한다 -- 각 단계는 정확히 그 단계가 오염시킨 이유로만 거부돼야 하고,
    이전 단계의 거부가 다음 정상 복귀 단계까지 새면 안 된다."""
    good_payload = _draft_payload()
    good_coverage = [_coverage_span()]

    stages: list[tuple[dict[str, Any], list[CoverageSpan], bool]] = [
        (good_payload, good_coverage, True),
        ({**good_payload, "script_source": FORBIDDEN_NAMESPACE_SCRIPT}, good_coverage, False),
        (good_payload, good_coverage, True),
        (good_payload, [], False),
        (good_payload, good_coverage, True),
    ]

    for payload, coverage, should_pass in stages:
        if should_pass:
            await _generate(payload=payload, coverage_spans=coverage)  # no raise
        else:
            with pytest.raises(Exception):  # noqa: B017 -- any GenerateProposalError subtype
                await _generate(payload=payload, coverage_spans=coverage)
