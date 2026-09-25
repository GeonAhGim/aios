"""Unit tests for `src/foundation/ai/factory/application/evaluate_proposal.py`
-- task-2647 AI-12 DoD ("임계 미달 -> FAIL 기록(I-07)"). D2 depth
(ADR-2026-09-09-C): negative >=3, failure injection 1, numeric performance
assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.ai.factory.application.evaluate_proposal import (
    DuplicateCheckTypeError,
    NoCheckResultsError,
    evaluate_proposal,
)
from src.foundation.ai.factory.contracts.v1 import DataScope, ProviderRef, StrategyProposal
from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.experiments.domain.lineage import ReproducibilityKeyCollisionError
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class _FakeExperimentRepository:
    """In-memory `ExperimentRepository` -- same shape as
    `tests/foundation/integration/experiments/test_application_integration.py`'s
    real-Postgres fixture, minus I/O (this leaf adds no new persistence, only
    a new caller of AI-11's already-tested `record_experiment`)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID], Experiment] = {}
        self.append_calls = 0
        self.fail_append: BaseException | None = None

    async def append(self, experiment: Experiment) -> None:
        self.append_calls += 1
        if self.fail_append is not None:
            raise self.fail_append
        self._rows[(experiment.tenant_id, experiment.experiment_id)] = experiment

    async def get(self, tenant_id: UUID, experiment_id: UUID) -> Experiment | None:
        return self._rows.get((tenant_id, experiment_id))

    async def find_by_reproducibility_key(
        self, tenant_id: UUID, reproducibility_key: str
    ) -> tuple[Experiment, ...]:
        return tuple(
            e
            for e in self._rows.values()
            if e.tenant_id == tenant_id and e.reproducibility_key == reproducibility_key
        )


def _proposal(**overrides: Any) -> StrategyProposal:
    base: dict[str, Any] = dict(
        proposal_id=uuid4(),
        script_source="input length: int = 14\nsignal go_long = length > 0\n",
        hypothesis="rsi mean reversion",
        data_scope=DataScope(
            instruments=frozenset({_INSTRUMENT}), tf=Timeframe.H1, span=(_START, _END)
        ),
        params={"length": 14},
        provider_ref=ProviderRef.ANTHROPIC,
        prompt_hash="0" * 64,
        created_by_token=uuid4(),
    )
    base.update(overrides)
    return StrategyProposal(**base)


def _passing_check(check_type: str = "backtest", **overrides: Any) -> CheckResult:
    base: dict[str, Any] = dict(
        check_type=check_type,
        outcome=Outcome.PASS,
        metrics={"sharpe_ratio": 1.5, "total_trades": 10},
        result_hash="a" * 64,
        policy_version="v1",
    )
    base.update(overrides)
    return CheckResult(**base)


def _hard_fail_check(check_type: str = "backtest", **overrides: Any) -> CheckResult:
    base: dict[str, Any] = dict(
        check_type=check_type,
        outcome=Outcome.FAIL,
        metrics={"sharpe_ratio": -0.4},
        hard_fail_reasons=["BACKTEST_LOOKAHEAD_VIOLATION"],
        result_hash="b" * 64,
        policy_version="v1",
    )
    base.update(overrides)
    return CheckResult(**base)


async def _evaluate(
    *,
    check_results: list[CheckResult],
    repository: _FakeExperimentRepository | None = None,
    proposal: StrategyProposal | None = None,
    tenant_id: UUID | None = None,
    inputs_hash: str = "c" * 64,
    reproducibility_key: str = "d" * 64,
):
    repo = repository if repository is not None else _FakeExperimentRepository()
    return (
        await evaluate_proposal(
            proposal=proposal if proposal is not None else _proposal(),
            check_results=check_results,
            inputs_hash=inputs_hash,
            reproducibility_key=reproducibility_key,
            repository=repo,
            tenant_id=tenant_id if tenant_id is not None else uuid4(),
            created_by=uuid4(),
        ),
        repo,
    )


# --- 정상 경로 ---


@pytest.mark.asyncio
async def test_evaluate_proposal_records_pass_and_returns_accepted() -> None:
    proposal = _proposal()
    evaluation, repo = await _evaluate(check_results=[_passing_check()], proposal=proposal)

    assert evaluation.accepted is True
    assert evaluation.hard_fail_reasons == ()
    assert evaluation.proposal_id == proposal.proposal_id
    assert repo.append_calls == 1
    recorded = await repo.get(list(repo._rows.keys())[0][0], evaluation.experiment_id)
    assert recorded is not None
    assert recorded.metrics["backtest"]["outcome"] == "PASS"


@pytest.mark.asyncio
async def test_evaluate_proposal_below_threshold_records_fail_not_accepted() -> None:
    """리프 DoD 그대로: 임계 미달(hard fail) -> FAIL이 기록되고 accepted=False."""
    evaluation, repo = await _evaluate(check_results=[_hard_fail_check()])

    assert evaluation.accepted is False
    assert evaluation.hard_fail_reasons == ("BACKTEST_LOOKAHEAD_VIOLATION",)
    assert repo.append_calls == 1  # FAIL is still recorded, not discarded


# --- 부정 테스트 (>=3) ---


@pytest.mark.asyncio
async def test_evaluate_proposal_rejects_empty_check_results() -> None:
    with pytest.raises(NoCheckResultsError):
        await _evaluate(check_results=[])


@pytest.mark.asyncio
async def test_evaluate_proposal_rejects_duplicate_check_type() -> None:
    with pytest.raises(DuplicateCheckTypeError) as exc_info:
        await _evaluate(check_results=[_passing_check(), _passing_check()])
    assert exc_info.value.check_type == "backtest"


@pytest.mark.asyncio
async def test_evaluate_proposal_propagates_reproducibility_key_collision() -> None:
    """AI-11 통합: 다른 inputs_hash로 같은 reproducibility_key를 재사용하면
    `record_experiment`의 계보 가드가 그대로 전파돼야 한다 -- 이 leaf가 그
    검사를 우회하거나 삼키지 않는다."""
    repo = _FakeExperimentRepository()
    tenant_id = uuid4()
    key = "e" * 64
    await _evaluate(
        check_results=[_passing_check()],
        repository=repo,
        tenant_id=tenant_id,
        reproducibility_key=key,
        inputs_hash="1" * 64,
    )
    with pytest.raises(ReproducibilityKeyCollisionError):
        await _evaluate(
            check_results=[_passing_check()],
            repository=repo,
            tenant_id=tenant_id,
            reproducibility_key=key,
            inputs_hash="2" * 64,
        )


# --- 실패 주입 ---


@pytest.mark.asyncio
async def test_evaluate_proposal_propagates_repository_append_failure() -> None:
    """실패 주입: 원장 저장 자체가 예외(DB 장애 등)를 내면 조용히 삼키지 않고
    그대로 전파돼야 한다 -- outcome을 계산해놓고 저장 실패를 성공으로
    위장하지 않는다."""
    repo = _FakeExperimentRepository()
    repo.fail_append = ConnectionError("ledger unreachable")
    with pytest.raises(ConnectionError):
        await _evaluate(check_results=[_passing_check()], repository=repo)


# --- 수치 성능 단언 ---

_EVALUATE_BUDGET_MS = 200.0
"""§7 SLO에 AI-12 전용 상한은 없다 -- AI-9 `generate_proposal`의 500ms
회귀 방지선보다 좁게 잡는다: 이 leaf는 공급자 호출이 없고 순수 계산 +
인메모리 원장 append 하나뿐이라 훨씬 빨라야 정상이다."""


async def _evaluate_latencies_ms(iterations: int = 20) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        await _evaluate(check_results=[_passing_check()])
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.asyncio
async def test_evaluate_proposal_p95_latency_within_budget() -> None:
    samples = await _evaluate_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-12 evaluate_proposal] p95={p95_ms:.2f}ms budget<{_EVALUATE_BUDGET_MS:.0f}ms")
    assert p95_ms < _EVALUATE_BUDGET_MS


# --- 게이트 적색 재현 ---


@pytest.mark.asyncio
async def test_gate_red_budget_actually_fails_past_budget() -> None:
    samples = await _evaluate_latencies_ms(iterations=5)
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms


@pytest.mark.asyncio
async def test_gate_red_progressive_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 check_results에서 시작해 한 번에 한 가지씩만 오염시켜
    재생한다 -- 각 단계는 정확히 그 단계가 오염시킨 이유로만 FAIL로
    거부돼야 하고, 이전 단계의 거부가 다음 정상 복귀 단계까지 새면 안 된다."""
    stages: list[tuple[list[CheckResult], bool]] = [
        ([_passing_check()], True),
        ([_hard_fail_check()], False),
        ([_passing_check()], True),
        ([_passing_check(), _hard_fail_check(check_type="point_in_time")], False),
        ([_passing_check()], True),
    ]

    for check_results, should_pass in stages:
        evaluation, _ = await _evaluate(check_results=check_results)
        assert evaluation.accepted is should_pass
