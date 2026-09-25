"""AppendAuditEvent 커맨드 — repo를 페이크로 대체한 순수 오케스트레이션
단위테스트. append_audit_event.py 실행 가능 statement 커버리지 0% -> 보강.

Spec: AIOSproject #79 §1/§2, #71 §3 FND-03."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.application.append_audit_event import (
    append_audit_event,
    event_to_view,
)
from src.foundation.evidence.contracts.v1 import (
    Classification as ContractClassification,
)
from src.foundation.evidence.contracts.v1 import (
    Outcome as ContractOutcome,
)
from src.foundation.evidence.contracts.v1 import RecordAuditEventCommand
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import UnsafePayloadError

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


class FakeAuditEventRepository:
    """In-memory stand-in for AuditEventRepository — records call args so
    tests can assert whether the repo was reached at all."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[dict[str, object]] = []

    async def append_event(
        self,
        *,
        tenant_id,
        aggregate_type,
        aggregate_id,
        aggregate_revision,
        action,
        outcome,
        actor_subject_id,
        trace_id,
        payload_hash,
        payload,
        classification,
    ) -> AuditEvent:
        self.calls.append({"action": action, "payload": payload})
        if self.fail_with is not None:
            raise self.fail_with
        sequence_no = len(self.calls)
        return AuditEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            sequence_no=sequence_no,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            action=action,
            outcome=outcome,
            actor_subject_id=actor_subject_id,
            trace_id=trace_id,
            payload_hash=payload_hash,
            payload=payload,
            classification=classification,
            previous_hash=None if sequence_no == 1 else "prev",
            event_hash=f"hash-{sequence_no}",
            occurred_at=NOW,
        )


def _command(**overrides) -> RecordAuditEventCommand:
    defaults = dict(
        tenant_id=uuid4(),
        aggregate_type="mandate",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="activate",
        outcome=ContractOutcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload={"purpose": "test"},
        classification=ContractClassification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)


async def test_append_audit_event_returns_mapped_view():
    repo = FakeAuditEventRepository()
    command = _command()

    view = await append_audit_event(repo, command)

    assert len(repo.calls) == 1
    assert view.tenant_id == command.tenant_id
    assert view.aggregate_type == command.aggregate_type
    assert view.action == command.action
    assert view.outcome == ContractOutcome.SUCCESS
    assert view.classification == ContractClassification.INTERNAL
    assert view.sequence_no == 1
    assert view.previous_hash is None
    assert view.event_hash == "hash-1"
    assert view.schema_version == "v1"


async def test_append_audit_event_computes_payload_hash():
    repo = FakeAuditEventRepository()
    command = _command(payload={"k": "v"})

    view = await append_audit_event(repo, command)

    from src.foundation.evidence.domain.rules import compute_payload_hash

    assert view.payload_hash == compute_payload_hash({"k": "v"})


def test_event_to_view_maps_all_fields():
    event = AuditEvent(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=3,
        aggregate_type="paper_deployment",
        aggregate_id=uuid4(),
        aggregate_revision=2,
        action="start",
        outcome=Outcome.DENIED,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="abc123",
        payload={"reason": "test"},
        classification=Classification.RESTRICTED,
        previous_hash="prev-hash",
        event_hash="this-hash",
        occurred_at=NOW,
    )

    view = event_to_view(event)

    assert view.id == event.id
    assert view.sequence_no == 3
    assert view.outcome == ContractOutcome.DENIED
    assert view.classification == ContractClassification.RESTRICTED
    assert view.previous_hash == "prev-hash"
    assert view.event_hash == "this-hash"
    assert view.occurred_at == NOW


# --- Negative tests (>=3) ---------------------------------------------------


async def test_unsafe_payload_key_rejected_and_repo_never_called():
    repo = FakeAuditEventRepository()
    command = _command(payload={"api_key": "leaked"})

    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, command)

    assert repo.calls == []


async def test_unsafe_nested_payload_key_rejected_and_repo_never_called():
    repo = FakeAuditEventRepository()
    command = _command(payload={"detail": {"password": "hunter2"}})

    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, command)

    assert repo.calls == []


def test_event_to_view_raises_when_occurred_at_missing():
    event = AuditEvent(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="activate",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="abc",
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=None,
        event_hash="",
        occurred_at=None,
    )

    with pytest.raises(AssertionError):
        event_to_view(event)


# --- Failure injection -------------------------------------------------------


async def test_repo_failure_propagates_without_being_swallowed():
    repo = FakeAuditEventRepository(fail_with=RuntimeError("db connection lost"))
    command = _command()

    with pytest.raises(RuntimeError, match="db connection lost"):
        await append_audit_event(repo, command)

    assert len(repo.calls) == 1


# --- Performance assertion (marker required — task-4674 QA note) ------------


@pytest.mark.perf
async def test_append_audit_event_orchestration_throughput():
    """Pure orchestration overhead (payload safety check + hash + mapping,
    repo I/O excluded via in-memory fake) for 500 sequential calls stays
    well under a 1s budget — guards against an accidental O(n^2) regression
    in payload validation or hashing on the hot append path."""
    repo = FakeAuditEventRepository()
    commands = [_command(payload={"i": i}) for i in range(500)]

    start = time.perf_counter()
    for command in commands:
        await append_audit_event(repo, command)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert len(repo.calls) == 500
