"""task-7955 / FND-08 REC-007: resolution must fail closed (I-07/I-10)."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
    resolve_reconciliation,
)
from src.foundation.reconciliation.domain.models import Classification
from tests.foundation.integration.reconciliation import test_postgres_repository as support

pool = support.pool
repo = support.repo
_state = support._state
_tenant = support._tenant


async def _resolve(repository, tenant_id, target_ref):
    return await resolve_reconciliation(
        repository,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        target_ref=target_ref,
        reason="Investigated discrepancy",
    )


async def test_negative_missing_target_rejected(repo, monkeypatch):
    target_ref = uuid4()
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)
    with pytest.raises(ReconciliationStateNotFoundError, match=str(target_ref)):
        await _resolve(repo, uuid4(), target_ref)
    transition.assert_not_awaited()
    assert await repo.get_state(target_ref) is None


async def test_negative_cross_tenant_resolution_rejected(pool, repo, monkeypatch):
    tenant_id = await _tenant(pool)
    other_tenant = await _tenant(pool)
    target_ref = uuid4()
    await repo.upsert_state(replace(
        _state(tenant_id, target_ref, Classification.MATERIAL_MISMATCH),
        blocking_reason="Material balance mismatch",
    ))
    before = await repo.get_state(target_ref)
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)
    with pytest.raises(CrossTenantReconciliationAccessError, match=str(target_ref)):
        await _resolve(repo, other_tenant, target_ref)
    transition.assert_not_awaited()
    assert await repo.get_state(target_ref) == before


@pytest.mark.parametrize("status", [
    Classification.HEALTHY,
    Classification.PENDING,
    Classification.MINOR_DIFFERENCE,
    Classification.RESOLVED,
])
async def test_negative_non_resolvable_status_rejected(pool, repo, monkeypatch, status):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    await repo.upsert_state(_state(tenant_id, target_ref, status))
    before = await repo.get_state(target_ref)
    transition = AsyncMock(wraps=repo.transition_state_status)
    monkeypatch.setattr(repo, "transition_state_status", transition)
    with pytest.raises(NotResolvableError, match=status.value):
        await _resolve(repo, tenant_id, target_ref)
    transition.assert_not_awaited()
    assert await repo.get_state(target_ref) == before


async def test_failure_injection_transition_error_preserves_blocking_state(pool, repo, monkeypatch):
    tenant_id = await _tenant(pool)
    target_ref = uuid4()
    await repo.upsert_state(replace(
        _state(tenant_id, target_ref, Classification.MATERIAL_MISMATCH),
        blocking_reason="Material balance mismatch",
    ))
    before = await repo.get_state(target_ref)
    failure = RuntimeError("injected storage outage")
    transition = AsyncMock(side_effect=failure)
    with monkeypatch.context() as patch:
        patch.setattr(repo, "transition_state_status", transition)
        with pytest.raises(RuntimeError, match="injected storage outage") as caught:
            await _resolve(repo, tenant_id, target_ref)
    assert caught.value is failure
    transition.assert_awaited_once_with(
        target_ref,
        expected_revision=before.revision,
        new_status=Classification.RESOLVED,
        blocking_reason=None,
        resolved_by=tenant_id,
        resolution_reason="Investigated discrepancy",
    )
    assert await repo.get_state(target_ref) == before
    resolved = await _resolve(repo, tenant_id, target_ref)
    assert resolved.aggregate_status.value == "RESOLVED"
    assert resolved.revision == before.revision + 1
