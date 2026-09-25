"""Tests for AppendAuditEvent command handler.

Spec: AIOSproject 79 §1/§2 (audit event recording, payload safety).
Coverage: append_audit_event(), event_to_view() with negative/edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.application.append_audit_event import (
    append_audit_event,
    event_to_view,
)
from src.foundation.evidence.contracts.v1 import (
    AuditEventView,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)
from src.foundation.evidence.domain.models import AuditEvent
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome
from src.foundation.evidence.domain.rules import UnsafePayloadError


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


def _make_audit_event(**overrides: object) -> AuditEvent:
    """Factory for AuditEvent domain model."""
    now = datetime.now(timezone.utc)
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="order",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="submit",
        outcome=DomainOutcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="abc123def456",
        payload={"key": "value"},
        classification=DomainClassification.INTERNAL,
        previous_hash=None,
        event_hash="hash123",
        occurred_at=now,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


def _make_command(**overrides: object) -> RecordAuditEventCommand:
    """Factory for RecordAuditEventCommand contract."""
    defaults: dict[str, object] = dict(
        tenant_id=uuid4(),
        aggregate_type="order",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="submit",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload={},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)  # type: ignore[arg-type]


class TestEventToView:
    """event_to_view() conversion tests."""

    def test_event_to_view_happy_path(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        view = event_to_view(event)

        assert isinstance(view, AuditEventView)
        assert view.id == event.id
        assert view.tenant_id == event.tenant_id
        assert view.sequence_no == event.sequence_no
        assert view.aggregate_type == event.aggregate_type
        assert view.aggregate_id == event.aggregate_id
        assert view.action == event.action
        assert view.outcome == Outcome.SUCCESS
        assert view.actor_subject_id == event.actor_subject_id
        assert view.trace_id == event.trace_id
        assert view.payload_hash == event.payload_hash
        assert view.payload == event.payload
        assert view.classification == Classification.INTERNAL
        assert view.previous_hash == event.previous_hash
        assert view.event_hash == event.event_hash
        assert view.occurred_at == now

    def test_event_to_view_system_event_no_tenant(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(tenant_id=None, occurred_at=now)
        view = event_to_view(event)
        assert view.tenant_id is None
        assert view.id == event.id

    def test_event_to_view_none_actor_subject_id(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(actor_subject_id=None, occurred_at=now)
        view = event_to_view(event)
        assert view.actor_subject_id is None

    def test_event_to_view_all_outcomes(self) -> None:
        now = datetime.now(timezone.utc)
        for outcome in DomainOutcome:
            event = _make_audit_event(outcome=outcome, occurred_at=now)
            view = event_to_view(event)
            assert view.outcome == Outcome(outcome.value)

    def test_event_to_view_all_classifications(self) -> None:
        now = datetime.now(timezone.utc)
        for classification in DomainClassification:
            event = _make_audit_event(classification=classification, occurred_at=now)
            view = event_to_view(event)
            assert view.classification == Classification(classification.value)

    def test_event_to_view_fails_if_occurred_at_none(self) -> None:
        event = _make_audit_event(occurred_at=None)
        with pytest.raises(AssertionError):
            event_to_view(event)

    def test_event_to_view_complex_payload(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "order_id": "ord123",
            "details": {"qty": 100, "price": 50.25},
            "tags": ["urgent", "review"],
        }
        event = _make_audit_event(payload=payload, occurred_at=now)
        view = event_to_view(event)
        assert view.payload == payload
        assert view.payload["details"]["qty"] == 100


class TestAppendAuditEvent:
    """append_audit_event() async function tests."""

    @pytest.mark.asyncio
    async def test_append_audit_event_happy_path(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command()
        result = await append_audit_event(repo, command)
        assert isinstance(result, AuditEventView)
        assert result.id == event.id
        assert result.tenant_id == event.tenant_id
        assert repo.last_call_kwargs is not None

    @pytest.mark.asyncio
    async def test_append_audit_event_validates_safe_payload(self) -> None:
        repo = MockAuditEventRepository()
        command = _make_command(payload={"password": "secret123"})
        with pytest.raises(UnsafePayloadError) as exc_info:
            await append_audit_event(repo, command)
        assert "password" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_append_audit_event_rejects_secret_key(self) -> None:
        repo = MockAuditEventRepository()
        command = _make_command(payload={"secret": "value"})
        with pytest.raises(UnsafePayloadError):
            await append_audit_event(repo, command)

    @pytest.mark.asyncio
    async def test_append_audit_event_rejects_token_key(self) -> None:
        repo = MockAuditEventRepository()
        command = _make_command(payload={"auth_token": "xyz"})
        with pytest.raises(UnsafePayloadError):
            await append_audit_event(repo, command)

    @pytest.mark.asyncio
    async def test_append_audit_event_rejects_private_key(self) -> None:
        repo = MockAuditEventRepository()
        for key in ["private_key", "privateKey", "private-key"]:
            command = _make_command(payload={key: "keydata"})
            with pytest.raises(UnsafePayloadError):
                await append_audit_event(repo, command)

    @pytest.mark.asyncio
    async def test_append_audit_event_rejects_nested_unsafe_payload(self) -> None:
        repo = MockAuditEventRepository()
        command = _make_command(payload={"user": {"password": "123"}})
        with pytest.raises(UnsafePayloadError):
            await append_audit_event(repo, command)

    @pytest.mark.asyncio
    async def test_append_audit_event_allows_safe_nested_payload(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        payload = {"user": {"name": "John", "email": "john@example.com"}}
        command = _make_command(payload=payload)
        result = await append_audit_event(repo, command)
        assert result is not None
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["payload"] == payload

    @pytest.mark.asyncio
    async def test_append_audit_event_computes_payload_hash(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        payload = {"key1": "value1", "key2": "value2"}
        command = _make_command(payload=payload)
        await append_audit_event(repo, command)
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["payload_hash"]
        assert isinstance(repo.last_call_kwargs["payload_hash"], str)
        assert len(repo.last_call_kwargs["payload_hash"]) == 64

    @pytest.mark.asyncio
    async def test_append_audit_event_passes_all_command_fields_to_repo(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        tenant_id = uuid4()
        actor_id = uuid4()
        trace_id = uuid4()
        aggregate_id = uuid4()
        command = _make_command(
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=aggregate_id,
            aggregate_revision=5,
            action="approve",
            outcome=Outcome.SUCCESS,
            actor_subject_id=actor_id,
            trace_id=trace_id,
            classification=Classification.CONFIDENTIAL,
            payload={"proposal": "change_limit"},
        )
        await append_audit_event(repo, command)
        kwargs = repo.last_call_kwargs
        assert kwargs is not None
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["aggregate_type"] == "mandate"
        assert kwargs["aggregate_id"] == aggregate_id
        assert kwargs["aggregate_revision"] == 5
        assert kwargs["action"] == "approve"
        assert kwargs["outcome"] == DomainOutcome.SUCCESS
        assert kwargs["actor_subject_id"] == actor_id
        assert kwargs["trace_id"] == trace_id
        assert kwargs["classification"] == DomainClassification.CONFIDENTIAL
        assert kwargs["payload"] == {"proposal": "change_limit"}

    @pytest.mark.asyncio
    async def test_append_audit_event_system_event_no_tenant(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(tenant_id=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(tenant_id=None)
        result = await append_audit_event(repo, command)
        assert result.tenant_id is None
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["tenant_id"] is None

    @pytest.mark.asyncio
    async def test_append_audit_event_empty_payload(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(payload={}, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(payload={})
        result = await append_audit_event(repo, command)
        assert result is not None
        assert repo.last_call_kwargs is not None
        assert repo.last_call_kwargs["payload"] == {}

    @pytest.mark.asyncio
    async def test_append_audit_event_all_outcomes(self) -> None:
        now = datetime.now(timezone.utc)
        repo = MockAuditEventRepository(event_to_return=_make_audit_event(occurred_at=now))
        for outcome in Outcome:
            command = _make_command(outcome=outcome)
            result = await append_audit_event(repo, command)
            assert result is not None

    @pytest.mark.asyncio
    async def test_append_audit_event_all_classifications(self) -> None:
        now = datetime.now(timezone.utc)
        repo = MockAuditEventRepository(event_to_return=_make_audit_event(occurred_at=now))
        for classification in Classification:
            command = _make_command(classification=classification)
            result = await append_audit_event(repo, command)
            assert result is not None

    @pytest.mark.asyncio
    async def test_append_audit_event_none_actor_subject_id(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(actor_subject_id=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(actor_subject_id=None)
        result = await append_audit_event(repo, command)
        assert result.actor_subject_id is None

    @pytest.mark.asyncio
    async def test_append_audit_event_none_aggregate_revision(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(aggregate_revision=None, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(aggregate_revision=None)
        result = await append_audit_event(repo, command)
        assert result.aggregate_revision is None

    @pytest.mark.asyncio
    async def test_append_audit_event_repo_exception_propagates(self) -> None:
        class FailingRepository:
            async def append_event(self, **kwargs: object) -> AuditEvent:
                raise ValueError("Database connection failed")

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
        command = _make_command()
        with pytest.raises(ValueError) as exc_info:
            await append_audit_event(repo, command)
        assert "connection" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_append_audit_event_hash_deterministic(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        payload = {"a": 1, "b": 2}
        command1 = _make_command(payload=payload)
        command2 = _make_command(payload=payload)
        await append_audit_event(repo, command1)
        assert repo.last_call_kwargs is not None
        hash1 = repo.last_call_kwargs["payload_hash"]
        await append_audit_event(repo, command2)
        assert repo.last_call_kwargs is not None
        hash2 = repo.last_call_kwargs["payload_hash"]
        assert hash1 == hash2

    @pytest.mark.asyncio
    async def test_append_audit_event_hash_different_payload(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command1 = _make_command(payload={"a": 1})
        await append_audit_event(repo, command1)
        assert repo.last_call_kwargs is not None
        hash1 = repo.last_call_kwargs["payload_hash"]
        command2 = _make_command(payload={"a": 2})
        await append_audit_event(repo, command2)
        assert repo.last_call_kwargs is not None
        hash2 = repo.last_call_kwargs["payload_hash"]
        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_append_audit_event_denied_outcome(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(outcome=DomainOutcome.DENIED, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(outcome=Outcome.DENIED)
        result = await append_audit_event(repo, command)
        assert result.outcome == Outcome.DENIED

    @pytest.mark.asyncio
    async def test_append_audit_event_error_outcome(self) -> None:
        now = datetime.now(timezone.utc)
        event = _make_audit_event(outcome=DomainOutcome.ERROR, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(outcome=Outcome.ERROR)
        result = await append_audit_event(repo, command)
        assert result.outcome == Outcome.ERROR

    @pytest.mark.asyncio
    async def test_append_audit_event_large_payload(self) -> None:
        now = datetime.now(timezone.utc)
        large_payload = {f"field_{i}": f"value_{i}" * 10 for i in range(100)}
        event = _make_audit_event(payload=large_payload, occurred_at=now)
        repo = MockAuditEventRepository(event_to_return=event)
        command = _make_command(payload=large_payload)
        result = await append_audit_event(repo, command)
        assert result is not None

    @pytest.mark.asyncio
    async def test_append_audit_event_api_key_case_insensitive(self) -> None:
        repo = MockAuditEventRepository()
        for key in ["API_KEY", "Api_Key", "api-key", "API-KEY"]:
            command = _make_command(payload={key: "secret"})
            with pytest.raises(UnsafePayloadError):
                await append_audit_event(repo, command)
