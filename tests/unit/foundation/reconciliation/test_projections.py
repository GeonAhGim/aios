"""Unit tests for build_reconciliation_state_list_view — pure assembly over
a fake repository (no DB). Covers empty/multi-state mapping, as_of invariant,
tenant scoping, and repository failure propagation."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.reconciliation.domain.models import Classification, ReconciliationState
from src.foundation.reconciliation.projections import (
    ReconciliationStateListView,
    build_reconciliation_state_list_view,
)


def _state(**overrides) -> ReconciliationState:
    now = datetime.now(timezone.utc)
    defaults = dict(
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
    return ReconciliationState(**defaults)


class _FakeRepo:
    def __init__(self, states: tuple[ReconciliationState, ...]) -> None:
        self._states = states
        self.list_states_calls: list = []
        self.raise_on_list: Exception | None = None

    async def list_states(self, tenant_id):
        self.list_states_calls.append(tenant_id)
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return self._states


async def test_build_reconciliation_state_list_view_empty_states_returns_empty_list():
    repo = _FakeRepo(())
    tenant_id = uuid4()

    view = await build_reconciliation_state_list_view(repo, tenant_id)

    assert isinstance(view, ReconciliationStateListView)
    assert view.states == []


async def test_build_reconciliation_state_list_view_maps_multiple_states_in_order():
    tenant_id = uuid4()
    state_a = _state(tenant_id=tenant_id, aggregate_status=Classification.HEALTHY)
    state_b = _state(tenant_id=tenant_id, aggregate_status=Classification.MATERIAL_MISMATCH)
    repo = _FakeRepo((state_a, state_b))

    view = await build_reconciliation_state_list_view(repo, tenant_id)

    assert len(view.states) == 2
    assert view.states[0].target_ref == state_a.target_ref
    assert view.states[0].aggregate_status.value == Classification.HEALTHY.value
    assert view.states[1].target_ref == state_b.target_ref
    assert view.states[1].aggregate_status.value == Classification.MATERIAL_MISMATCH.value


async def test_build_reconciliation_state_list_view_passes_tenant_id_to_repo():
    repo = _FakeRepo(())
    tenant_id = uuid4()

    await build_reconciliation_state_list_view(repo, tenant_id)

    assert repo.list_states_calls == [tenant_id]


async def test_build_reconciliation_state_list_view_as_of_is_utc_aware():
    repo = _FakeRepo(())
    before = datetime.now(timezone.utc)

    view = await build_reconciliation_state_list_view(repo, uuid4())

    after = datetime.now(timezone.utc)
    assert view.as_of.tzinfo is not None
    assert view.as_of.utcoffset() == timezone.utc.utcoffset(None)
    assert before <= view.as_of <= after


async def test_build_reconciliation_state_list_view_propagates_repository_failure():
    repo = _FakeRepo(())
    repo.raise_on_list = RuntimeError("connection pool exhausted")

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await build_reconciliation_state_list_view(repo, uuid4())


# ---- 수치 성능 단언 ----
# I/O는 repo.list_states가 담당하고 이 모듈 자체는 순수 조립(map)만 한다.
# 조립 hot path에 성능 단언을 건다.


@pytest.mark.perf
async def test_build_reconciliation_state_list_view_hot_path_performance():
    tenant_id = uuid4()
    states = tuple(_state(tenant_id=tenant_id) for _ in range(200))
    repo = _FakeRepo(states)

    start = time.perf_counter()
    for _ in range(50):
        await build_reconciliation_state_list_view(repo, tenant_id)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
