"""Tests for record_command_event — advanced scenarios (errors, trace ID, enums).

Failure injection, trace ID correlation, and enum value coverage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.observability.context import RequestContext
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification, Outcome
from src.foundation.evidence.domain.models import AuditEvent
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome
from src.foundation.evidence.domain.rules import UnsafePayloadError
from tests.foundation.unit.evidence.application.conftest import (
    MockAuditEventRepository,
    make_audit_event,
)


class TestRecordCommandEventFailureInjection:
    """Failure injection and error propagation."""

    @pytest.mark.asyncio
    async def test_record_command_event_propagates_repository_error(self, monkeypatch) -> None:
        """Repository errors should propagate."""
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

        class FailingRepository:
            async def append_event(self, **kwargs: object) -> AuditEvent:
                raise RuntimeError("Database connection lost")

            async def list_timeline(
                self,
                tenant_id: object,
                *,
                cursor: str | None = None,
                limit: int = 1,
                **kwargs: object,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def list_chain_for_verification(self, tenant_id: object) -> list[AuditEvent]:
                return []

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: object, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = FailingRepository()
        with pytest.raises(RuntimeError, match="Database connection lost"):
            await record_command_event(
                repo,
                tenant_id=tenant_id,
                aggregate_type="mandate",
                aggregate_id=aggregate_id,
                action="activate",
                actor_subject_id=actor_id,
            )

    @pytest.mark.asyncio
    async def test_record_command_event_propagates_unsafe_payload_error(self, monkeypatch) -> None:
        """Unsafe payloads should raise UnsafePayloadError from append_audit_event."""
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

        repo = MockAuditEventRepository()
        with pytest.raises(UnsafePayloadError):
            await record_command_event(
                repo,
                tenant_id=tenant_id,
                aggregate_type="mandate",
                aggregate_id=aggregate_id,
                action="activate",
                actor_subject_id=actor_id,
                payload={"password": "secret123"},
            )


class TestRecordCommandEventTraceIdInjection:
    """Trace ID correlation via request context."""

    @pytest.mark.asyncio
    async def test_record_command_event_uses_trace_id_from_request_context(
        self, monkeypatch
    ) -> None:
        """Trace ID should be injected from current request context, not regenerated."""
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

        await record_command_event(
            repo,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
        )

        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["trace_id"] == trace_id

    @pytest.mark.asyncio
    async def test_record_command_event_different_trace_ids_for_different_requests(
        self, monkeypatch
    ) -> None:
        """Different requests should have different trace IDs."""
        trace_id_1 = uuid4()
        trace_id_2 = uuid4()
        tenant_id = uuid4()
        actor_id = uuid4()
        aggregate_id = uuid4()

        call_count = 0

        def mock_context():
            nonlocal call_count
            call_count += 1
            trace_id = trace_id_1 if call_count == 1 else trace_id_2
            return RequestContext(
                request_id=uuid4().hex,
                trace_id=trace_id,
            )

        monkeypatch.setattr(
            "src.foundation.evidence.application.record_command_event.current_request_context",
            mock_context,
        )

        now = datetime.now(timezone.utc)

        event1 = make_audit_event(trace_id=trace_id_1, occurred_at=now)
        repo1 = MockAuditEventRepository(event_to_return=event1)
        await record_command_event(
            repo1,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
        )

        event2 = make_audit_event(trace_id=trace_id_2, occurred_at=now)
        repo2 = MockAuditEventRepository(event_to_return=event2)
        await record_command_event(
            repo2,
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            action="activate",
            actor_subject_id=actor_id,
        )

        assert repo1.last_call_kwargs is not None
        assert repo2.last_call_kwargs is not None
        assert repo1.last_call_kwargs["trace_id"] == trace_id_1
        assert repo2.last_call_kwargs["trace_id"] == trace_id_2
        assert repo1.last_call_kwargs["trace_id"] != repo2.last_call_kwargs["trace_id"]


class TestRecordCommandEventAllOutcomeValues:
    """All outcome enum values should be supported."""

    @pytest.mark.asyncio
    async def test_record_command_event_all_outcomes(self, monkeypatch) -> None:
        """All Outcome variants should pass through."""
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
        for outcome in Outcome:
            domain_outcome = DomainOutcome(outcome.value)
            event = make_audit_event(outcome=domain_outcome, occurred_at=now)
            repo = MockAuditEventRepository(event_to_return=event)
            result = await record_command_event(
                repo,
                tenant_id=tenant_id,
                aggregate_type="mandate",
                aggregate_id=aggregate_id,
                action="test",
                actor_subject_id=actor_id,
                outcome=outcome,
            )
            assert result.outcome == outcome


class TestRecordCommandEventAllClassificationValues:
    """All classification enum values should be supported."""

    @pytest.mark.asyncio
    async def test_record_command_event_all_classifications(self, monkeypatch) -> None:
        """All Classification variants should pass through."""
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
        for classification in Classification:
            domain_classification = DomainClassification(classification.value)
            event = make_audit_event(classification=domain_classification, occurred_at=now)
            repo = MockAuditEventRepository(event_to_return=event)
            result = await record_command_event(
                repo,
                tenant_id=tenant_id,
                aggregate_type="mandate",
                aggregate_id=aggregate_id,
                action="test",
                actor_subject_id=actor_id,
                classification=classification,
            )
            assert result.classification == classification
