"""Shared test utilities for record_command_event tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from src.foundation.evidence.domain.models import AuditEvent
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome


class MockAuditEventRepository:
    """Minimal async repository mock for testing."""

    def __init__(self, event_to_return: AuditEvent | None = None) -> None:
        self.event_to_return = event_to_return
        self.last_call_kwargs: dict[str, object] | None = None

    async def append_event(self, **kwargs: object) -> AuditEvent:
        self.last_call_kwargs = kwargs
        if self.event_to_return:
            return self.event_to_return
        raise RuntimeError("Mock not configured")

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


def make_audit_event(**overrides: object) -> AuditEvent:
    """Factory for AuditEvent domain model."""
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="activate",
        outcome=DomainOutcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="abc123def456",
        payload={},
        classification=DomainClassification.INTERNAL,
        previous_hash=None,
        event_hash="hash123",
        occurred_at=now,
    )
    defaults.update(overrides)
    return cast(AuditEvent, AuditEvent(**defaults))
