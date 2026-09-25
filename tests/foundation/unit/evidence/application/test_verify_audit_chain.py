"""VerifyAuditChain (AUD-003) 오케스트레이션 단위테스트 — repo를 페이크로
대체. verify_audit_chain.py 실행 가능 statement 커버리지 0% -> 보강.

Spec: AIOSproject #79 §4 SLI "chain verification success"."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import ChainIntegrityError, compute_event_hash

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


class FakeAuditEventRepository:
    """In-memory stand-in for AuditEventRepository."""

    def __init__(
        self,
        *,
        events: list[AuditEvent] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.fail_with = fail_with
        self.calls: list[object] = []

    async def list_chain_for_verification(self, tenant_id):
        self.calls.append(tenant_id)
        if self.fail_with is not None:
            raise self.fail_with
        return self.events


def _event(*, sequence_no: int, previous_hash: str | None, **overrides) -> AuditEvent:
    tenant_id = overrides.pop("tenant_id", None)
    aggregate_id = overrides.pop("aggregate_id", uuid4())
    aggregate_type = overrides.pop("aggregate_type", "mandate")
    action = overrides.pop("action", "activate")
    outcome = overrides.pop("outcome", Outcome.SUCCESS)
    payload_hash = overrides.pop("payload_hash", "abc")
    classification = overrides.pop("classification", Classification.INTERNAL)
    occurred_at = overrides.pop("occurred_at", NOW)

    event_hash = compute_event_hash(
        previous_hash=previous_hash,
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        action=action,
        outcome=outcome,
        payload_hash=payload_hash,
        classification=classification,
        occurred_at=occurred_at,
    )
    return AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_revision=1,
        action=action,
        outcome=outcome,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash=payload_hash,
        payload={},
        classification=classification,
        previous_hash=previous_hash,
        event_hash=event_hash,
        occurred_at=occurred_at,
        **overrides,
    )


def _valid_chain(n: int, *, tenant_id=None) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    prev_hash: str | None = None
    for i in range(n):
        ev = _event(sequence_no=i, previous_hash=prev_hash, tenant_id=tenant_id)
        events.append(ev)
        prev_hash = ev.event_hash
    return events


# --- Happy path ---------------------------------------------------------------


async def test_empty_chain_returns_none_quietly():
    repo = FakeAuditEventRepository(events=[])

    result = await verify_audit_chain(repo, uuid4())

    assert result is None


async def test_valid_multi_event_chain_returns_none():
    tenant_id = uuid4()
    repo = FakeAuditEventRepository(events=_valid_chain(5, tenant_id=tenant_id))

    result = await verify_audit_chain(repo, tenant_id)

    assert result is None
    assert repo.calls == [tenant_id]


async def test_none_tenant_id_queries_system_chain():
    repo = FakeAuditEventRepository(events=_valid_chain(2, tenant_id=None))

    await verify_audit_chain(repo, None)

    assert repo.calls == [None]


# --- Negative tests (>=3) ------------------------------------------------------


async def test_broken_previous_hash_raises_chain_integrity_error():
    events = _valid_chain(2)
    tampered = _event(
        sequence_no=events[1].sequence_no,
        previous_hash="0" * 64,
        aggregate_id=events[1].aggregate_id,
    )
    repo = FakeAuditEventRepository(events=[events[0], tampered])

    with pytest.raises(ChainIntegrityError) as exc_info:
        await verify_audit_chain(repo, uuid4())

    assert "previous_hash" in exc_info.value.detail


async def test_tampered_event_hash_raises_chain_integrity_error():
    events = _valid_chain(1)
    corrupted = AuditEvent(
        id=events[0].id,
        tenant_id=events[0].tenant_id,
        sequence_no=events[0].sequence_no,
        aggregate_type=events[0].aggregate_type,
        aggregate_id=events[0].aggregate_id,
        aggregate_revision=events[0].aggregate_revision,
        action=events[0].action,
        outcome=events[0].outcome,
        actor_subject_id=events[0].actor_subject_id,
        trace_id=events[0].trace_id,
        payload_hash=events[0].payload_hash,
        payload=events[0].payload,
        classification=events[0].classification,
        previous_hash=events[0].previous_hash,
        event_hash="deadbeef" * 8,
        occurred_at=events[0].occurred_at,
    )
    repo = FakeAuditEventRepository(events=[corrupted])

    with pytest.raises(ChainIntegrityError):
        await verify_audit_chain(repo, uuid4())


async def test_missing_occurred_at_raises_chain_integrity_error():
    events = _valid_chain(1)
    corrupted = AuditEvent(
        id=events[0].id,
        tenant_id=events[0].tenant_id,
        sequence_no=events[0].sequence_no,
        aggregate_type=events[0].aggregate_type,
        aggregate_id=events[0].aggregate_id,
        aggregate_revision=events[0].aggregate_revision,
        action=events[0].action,
        outcome=events[0].outcome,
        actor_subject_id=events[0].actor_subject_id,
        trace_id=events[0].trace_id,
        payload_hash=events[0].payload_hash,
        payload=events[0].payload,
        classification=events[0].classification,
        previous_hash=events[0].previous_hash,
        event_hash=events[0].event_hash,
        occurred_at=None,
    )
    repo = FakeAuditEventRepository(events=[corrupted])

    with pytest.raises(ChainIntegrityError) as exc_info:
        await verify_audit_chain(repo, uuid4())

    assert "occurred_at" in exc_info.value.detail


async def test_second_event_missing_from_chain_raises():
    """Deleting the middle link leaves the tail's previous_hash orphaned."""
    events = _valid_chain(3)
    repo = FakeAuditEventRepository(events=[events[0], events[2]])

    with pytest.raises(ChainIntegrityError):
        await verify_audit_chain(repo, uuid4())


# --- Failure injection ----------------------------------------------------------


async def test_repository_failure_propagates_without_being_swallowed():
    repo = FakeAuditEventRepository(fail_with=RuntimeError("db connection lost"))

    with pytest.raises(RuntimeError, match="db connection lost"):
        await verify_audit_chain(repo, uuid4())

    assert len(repo.calls) == 1


# --- Performance assertion (marker required — task-4674 QA note) ---------------


@pytest.mark.perf
async def test_verify_audit_chain_throughput_for_large_chain():
    """A 500-link valid chain verifies well under a 1s budget — guards the
    AUD-003 verification path against an accidental quadratic regression."""
    repo = FakeAuditEventRepository(events=_valid_chain(500))

    start = time.perf_counter()
    await verify_audit_chain(repo, uuid4())
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
