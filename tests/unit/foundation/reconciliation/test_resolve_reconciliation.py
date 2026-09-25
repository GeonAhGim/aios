"""Unit tests for resolve_reconciliation — pure application command, fake
repository (no DB). Covers state_to_view mapping and every guard branch."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
    resolve_reconciliation,
    state_to_view,
)
from src.foundation.reconciliation.domain.models import Classification, ReconciliationState


def _state(**overrides) -> ReconciliationState:
    now = datetime.now(timezone.utc)
    defaults = dict(
        target_ref=uuid4(),
        target_type="PAPER_DEPLOYMENT",
        tenant_id=uuid4(),
        aggregate_status=Classification.MATERIAL_MISMATCH,
        last_healthy_at=None,
        last_checked_at=now,
        blocking_reason="INTEGRITY_RECONCILIATION_MISMATCH:MATERIAL_MISMATCH",
        revision=0,
        safety_control_id=None,
    )
    defaults.update(overrides)
    return ReconciliationState(**defaults)


class _FakeRepo:
    def __init__(self, state: ReconciliationState | None) -> None:
        self._state = state
        self.transition_calls: list[dict] = []
        self.raise_on_transition: Exception | None = None

    async def get_state(self, target_ref):
        return self._state

    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ):
        if self.raise_on_transition is not None:
            raise self.raise_on_transition
        self.transition_calls.append(
            dict(
                target_ref=target_ref,
                expected_revision=expected_revision,
                new_status=new_status,
                blocking_reason=blocking_reason,
                resolved_by=resolved_by,
                resolution_reason=resolution_reason,
            )
        )
        return ReconciliationState(
            target_ref=self._state.target_ref,
            target_type=self._state.target_type,
            tenant_id=self._state.tenant_id,
            aggregate_status=new_status,
            last_healthy_at=self._state.last_healthy_at,
            last_checked_at=self._state.last_checked_at,
            blocking_reason=blocking_reason,
            revision=self._state.revision + 1,
            safety_control_id=self._state.safety_control_id,
            resolved_by=resolved_by,
            resolution_reason=resolution_reason,
        )


def test_state_to_view_maps_all_fields():
    state = _state()

    view = state_to_view(state)

    assert view.target_ref == state.target_ref
    assert view.target_type == state.target_type
    assert view.aggregate_status.value == state.aggregate_status.value
    assert view.last_checked_at == state.last_checked_at
    assert view.blocking_reason == state.blocking_reason
    assert view.revision == state.revision


async def test_resolve_reconciliation_not_found_raises():
    repo = _FakeRepo(None)
    tenant_id = uuid4()
    target_ref = uuid4()

    with pytest.raises(ReconciliationStateNotFoundError):
        await resolve_reconciliation(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=uuid4(),
            target_ref=target_ref,
            reason="not found case",
        )


async def test_resolve_reconciliation_cross_tenant_raises():
    owner_id = uuid4()
    attacker_id = uuid4()
    state = _state(tenant_id=owner_id)
    repo = _FakeRepo(state)

    with pytest.raises(CrossTenantReconciliationAccessError):
        await resolve_reconciliation(
            repo,
            tenant_id=attacker_id,
            actor_subject_id=attacker_id,
            target_ref=state.target_ref,
            reason="cross tenant attempt",
        )
    assert repo.transition_calls == []


@pytest.mark.parametrize(
    "status",
    [
        Classification.HEALTHY,
        Classification.PENDING,
        Classification.MINOR_DIFFERENCE,
        Classification.RESOLVED,
    ],
)
async def test_resolve_reconciliation_not_resolvable_status_raises(status):
    state = _state(aggregate_status=status)
    repo = _FakeRepo(state)

    with pytest.raises(NotResolvableError):
        await resolve_reconciliation(
            repo,
            tenant_id=state.tenant_id,
            actor_subject_id=uuid4(),
            target_ref=state.target_ref,
            reason="wrong status",
        )
    assert repo.transition_calls == []


@pytest.mark.parametrize(
    "status",
    [
        Classification.MATERIAL_MISMATCH,
        Classification.PROVIDER_UNAVAILABLE,
        Classification.INVESTIGATING,
    ],
)
async def test_resolve_reconciliation_success_transitions_to_resolved(status):
    state = _state(aggregate_status=status)
    repo = _FakeRepo(state)
    actor_id = uuid4()

    result = await resolve_reconciliation(
        repo,
        tenant_id=state.tenant_id,
        actor_subject_id=actor_id,
        target_ref=state.target_ref,
        reason="확인 완료",
    )

    assert result.aggregate_status.value == Classification.RESOLVED.value
    assert result.blocking_reason is None
    assert len(repo.transition_calls) == 1
    call = repo.transition_calls[0]
    assert call["expected_revision"] == state.revision
    assert call["new_status"] == Classification.RESOLVED
    assert call["blocking_reason"] is None
    assert call["resolved_by"] == actor_id
    assert call["resolution_reason"] == "확인 완료"


async def test_resolve_reconciliation_propagates_repository_failure():
    state = _state(aggregate_status=Classification.MATERIAL_MISMATCH)
    repo = _FakeRepo(state)
    repo.raise_on_transition = RuntimeError("optimistic lock conflict")

    with pytest.raises(RuntimeError, match="optimistic lock conflict"):
        await resolve_reconciliation(
            repo,
            tenant_id=state.tenant_id,
            actor_subject_id=uuid4(),
            target_ref=state.target_ref,
            reason="failure injection",
        )
