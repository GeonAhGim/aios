"""Tests for record_command_event function — happy path and boundary conditions.

Spec: Full-audit review (agent-platform-12, 2026-09-02) §6 — public entry point
that wraps append_audit_event with automatic trace_id injection from request context.

Coverage: happy path scenarios and boundary cases (None tenant/payload/actor).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.observability.context import RequestContext
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import (
    AuditEventView,
    Classification,
    Outcome,
)
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome
from tests.foundation.unit.evidence.application.conftest import (
    MockAuditEventRepository,
    make_audit_event,
)


class TestRecordCommandEventHappyPath:
    """Happy path scenarios."""

    @pytest.mark.asyncio
    async def test_record_command_event_happy_path(self, monkeypatch) -> None:
        """Minimal case with all required fields."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(trace_id=trace_id, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
        )

        assert isinstance(result, AuditEventView)
        assert result.trace_id == trace_id
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["trace_id"] == trace_id

    @pytest.mark.asyncio
    async def test_record_command_event_with_all_optional_fields(self, monkeypatch) -> None:
        """Call with all optional fields specified."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(
            outcome=DomainOutcome.DENIED,
            classification=DomainClassification.CONFIDENTIAL,
            aggregate_revision=5,
            occurred_at=now,
        )
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="safety_control",
            aggregate_id=aggregate_id,
            aggregate_revision=5,
            action="deactivate",
            actor_subject_id=actor_id,
            outcome=Outcome.DENIED,
            classification=Classification.CONFIDENTIAL,
            payload={"reason": "manual override"},
        )

        assert result.outcome == Outcome.DENIED
        assert result.classification == Classification.CONFIDENTIAL
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["aggregate_revision"] == 5

    @pytest.mark.asyncio
    async def test_record_command_event_defaults_outcome_to_success(self, monkeypatch) -> None:
        """Default outcome is SUCCESS when not specified."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(outcome=DomainOutcome.SUCCESS, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="pause",
            actor_subject_id=actor_id,
        )

        assert result.outcome == Outcome.SUCCESS
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["outcome"] == DomainOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_record_command_event_defaults_classification_to_internal(
        self, monkeypatch
    ) -> None:
        """Default classification is INTERNAL when not specified."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(classification=DomainClassification.INTERNAL, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="resume",
            actor_subject_id=actor_id,
        )

        assert result.classification == Classification.INTERNAL
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["classification"] == DomainClassification.INTERNAL


class TestRecordCommandEventBoundaryConditions:
    """Boundary cases and edge conditions."""

    @pytest.mark.asyncio
    async def test_record_command_event_none_tenant_id(self, monkeypatch) -> None:
        """System events (no tenant) should pass through."""
        trace_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(tenant_id=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=None,
            aggregate_type="system",
            aggregate_id=aggregate_id,
            action="startup",
            actor_subject_id=actor_id,
        )

        assert result.tenant_id is None
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["tenant_id"] is None

    @pytest.mark.asyncio
    async def test_record_command_event_none_actor_subject_id(self, monkeypatch) -> None:
        """System/automated events with no actor."""
        trace_id = uuid4()
        tenant_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(actor_subject_id=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="auto_resume",
            actor_subject_id=None,
        )

        assert result.actor_subject_id is None

    @pytest.mark.asyncio
    async def test_record_command_event_none_payload_defaults_to_empty_dict(
        self, monkeypatch
    ) -> None:
        """When payload not specified, defaults to empty dict."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(payload={}, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
        )

        assert result.payload == {}
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["payload"] == {}

    @pytest.mark.asyncio
    async def test_record_command_event_empty_payload_dict(self, monkeypatch) -> None:
        """Explicitly empty payload."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(payload={}, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
            payload={},
        )

        assert result.payload == {}

    @pytest.mark.asyncio
    async def test_record_command_event_none_aggregate_revision(self, monkeypatch) -> None:
        """Aggregate revision can be None."""
        trace_id = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        mock_context = RequestContext(
            request_id=uuid4().hex,
            trace_id=trace_id,
        )
        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            lambda: mock_context,
        )

        now = datetime.now(timezone.utc)
        event = make_audit_event(aggregate_revision=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)

        result = await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
            aggregate_revision=None,
        )

        assert result.aggregate_revision is None
