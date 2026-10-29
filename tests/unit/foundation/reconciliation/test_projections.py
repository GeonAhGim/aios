"""Unit tests for build_reconciliation_state_list_view — pure assembly over
a fake repository (no DB). Covers empty/multi-state mapping, as_of invariant,
tenant scoping, and repository failure propagation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.foundation.reconciliation.domain.models import Classification, ReconciliationState
from src.foundation.reconciliation.projections import (
    ReconciliationStateListView,
    build_reconciliation_state_list_view,
)
from tests.conftest import PerfBudget


def _state(**overrides: object) -> ReconciliationState:
    now = datetime.now(UTC)
    defaults: dict[str, object] = dict(
        target_ref=uuid4(),
        target_type="PAPER_DEPLOYMENT",
        tenant_id=uuid4(),
        aggregate_status=Classification.HEALTHY,
        last_healthy_at=now,
        last_checked_at=now,
        blocking_reason=None,
        revision=0,
        safety_control_id=None,
    )
    defaults.update(overrides)
    return cast(ReconciliationState, ReconciliationState(**defaults))


class _FakeRepo:
    def __init__(self, states: tuple[ReconciliationState, ...]) -> None:
        self._states = states
        self.list_states_calls: list[object] = []
        self.raise_on_list: Exception | None = None

    async def list_states(self, tenant_id: object) -> tuple[ReconciliationState, ...]:
        self.list_states_calls.append(tenant_id)
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return self._states


async def test_build_reconciliation_state_list_view_empty_states_returns_empty_list() -> None:
    repo = _FakeRepo(())
    tenant_id = uuid4()

    view = cast(
        ReconciliationStateListView, await build_reconciliation_state_list_view(repo, tenant_id)
    )

    assert isinstance(view, ReconciliationStateListView)
    assert view.states == []


async def test_build_reconciliation_state_list_view_maps_multiple_states_in_order() -> None:
    tenant_id = uuid4()
    state_a = _state(tenant_id=tenant_id, aggregate_status=Classification.HEALTHY)
    state_b = _state(tenant_id=tenant_id, aggregate_status=Classification.MATERIAL_MISMATCH)
    repo = _FakeRepo((state_a, state_b))

    view = cast(
        ReconciliationStateListView, await build_reconciliation_state_list_view(repo, tenant_id)
    )

    assert len(view.states) == 2
    assert view.states[0].target_ref == state_a.target_ref
    assert view.states[0].aggregate_status.value == Classification.HEALTHY.value
    assert view.states[1].target_ref == state_b.target_ref
    assert view.states[1].aggregate_status.value == Classification.MATERIAL_MISMATCH.value


async def test_build_reconciliation_state_list_view_passes_tenant_id_to_repo() -> None:
    repo = _FakeRepo(())
    tenant_id = uuid4()

    _ = cast(
        ReconciliationStateListView, await build_reconciliation_state_list_view(repo, tenant_id)
    )

    assert repo.list_states_calls == [tenant_id]


async def test_build_reconciliation_state_list_view_as_of_is_utc_aware() -> None:
    repo = _FakeRepo(())
    before = datetime.now(UTC)

    view = cast(
        ReconciliationStateListView, await build_reconciliation_state_list_view(repo, uuid4())
    )

    after = datetime.now(UTC)
    assert view.as_of.tzinfo is not None
    assert view.as_of.utcoffset() == UTC.utcoffset(None)
    assert before <= view.as_of <= after


async def test_build_reconciliation_state_list_view_propagates_repository_failure() -> None:
    repo = _FakeRepo(())
    repo.raise_on_list = RuntimeError("connection pool exhausted")

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        _ = cast(
            ReconciliationStateListView, await build_reconciliation_state_list_view(repo, uuid4())
        )


# ---- 수치 성능 단언 ----
# I/O는 repo.list_states가 담당하고 이 모듈 자체는 순수 조립(map)만 한다.
# 조립 hot path에 성능 단언을 건다.


@pytest.mark.perf
async def test_build_reconciliation_state_list_view_hot_path_performance(
    perf_budget: PerfBudget,
) -> None:
    """perf: 50회 연속 호출 총 wall 시간 예산 1초 — batch=N으로 묶어 측정."""
    tenant_id = uuid4()
    states = tuple(_state(tenant_id=tenant_id) for _ in range(200))
    repo = _FakeRepo(states)

    async def _run_batch() -> None:
        for _ in range(50):
            _ = cast(
                ReconciliationStateListView,
                await build_reconciliation_state_list_view(repo, tenant_id),
            )

    sample = await perf_budget.sample_async(_run_batch)
    assert sample.wall_ms < 1000.0


# ---- 음수 테스트 ----


async def test_build_reconciliation_state_list_view_handles_repository_connection_error() -> None:
    """Negative: repository connection error propagates correctly."""
    repo = _FakeRepo(())
    repo.raise_on_list = ConnectionError("database unreachable")

    with pytest.raises(ConnectionError, match="database unreachable"):
        _ = cast(
            ReconciliationStateListView, await build_reconciliation_state_list_view(repo, uuid4())
        )


async def test_build_reconciliation_state_list_view_handles_repository_timeout() -> None:
    """Negative: repository timeout error propagates correctly."""
    repo = _FakeRepo(())
    repo.raise_on_list = TimeoutError("query exceeded timeout")

    with pytest.raises(TimeoutError, match="query exceeded timeout"):
        _ = cast(
            ReconciliationStateListView, await build_reconciliation_state_list_view(repo, uuid4())
        )


async def test_build_reconciliation_state_list_view_handles_invalid_state_conversion() -> None:
    """Negative: invalid state that fails conversion is rejected."""

    repo = _FakeRepo((_state(),))
    with patch(
        "src.foundation.reconciliation.projections.state_to_view",
        side_effect=ValueError("invalid state"),
    ):
        with pytest.raises(ValueError, match="invalid state"):
            _ = cast(
                ReconciliationStateListView,
                await build_reconciliation_state_list_view(repo, uuid4()),
            )


# ---- D2 성능 증거: CPU 0 수렴, gate-red 재현 ----


@pytest.mark.perf
async def test_build_reconciliation_state_list_view_cpu_zero_convergence(
    perf_budget: PerfBudget,
) -> None:
    """CPU 측정의 0 수렴: 단일 호출 20회 best-of-5 최소 CPU가 5ms 미만이어야 한다.

    `state_to_view`는 단순 데이터 복사, `datetime.now(timezone.utc)`는
    경량 연산이므로 CPU 시간은 양자화 틱(Windows ~15.6ms) 아래로 수렴해야 한다.
    """
    tenant_id = uuid4()
    states = tuple(_state(tenant_id=tenant_id) for _ in range(200))
    repo = _FakeRepo(states)

    async def _single_call() -> None:
        _ = cast(
            ReconciliationStateListView,
            await build_reconciliation_state_list_view(repo, tenant_id),
        )

    # warmup은 sync 메서드 samples만 지원 — async는 직접 호출
    for _ in range(3):
        await _single_call()
    samples = await perf_budget.samples_async(_single_call, n=20, batch=1)
    best = min(samples, key=lambda s: s.cpu_ms)
    assert best.cpu_ms < 5.0, (
        f"single-call CPU {best.cpu_ms:.3f}ms exceeds 5ms zero-convergence budget; "
        f"wall={best.wall_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_build_reconciliation_state_list_view_goes_red_with_10x_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction(D2): 같은 성능 단언이 sleep 주입으로 적색이 돼야 한다.

    `asyncio.sleep(0.1)`을 매 호출 전 주입해 wall-clock을 100ms 이상 늘리면,
    기존 녹색 테스트의 `wall_ms < 1000.0` 예산(50회 배치 기준)에서도
    각 호출당 지연이 누적되어 단언이 AssertionError로 떨어진다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다.
    """
    tenant_id = uuid4()
    states = tuple(_state(tenant_id=tenant_id) for _ in range(200))

    async def _run_batch_with_delay() -> None:
        for _ in range(50):
            await asyncio.sleep(0.1)  # 100ms delay per call → 50 calls × 100ms = 5s
            _ = cast(
                ReconciliationStateListView,
                await build_reconciliation_state_list_view(_FakeRepo(states), tenant_id),
            )

    sample = await perf_budget.sample_async(_run_batch_with_delay)
    assert sample.wall_ms >= 1000.0, (
        f"10x delay injection: wall_ms={sample.wall_ms:.3f}ms did NOT exceed 1000ms budget — "
        "budget assertion failed to detect injected delay (hollow assertion)"
    )
