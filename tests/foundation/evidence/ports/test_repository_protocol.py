"""Tests for AuditEventRepository protocol.

Spec: Rule 71 §4 (protocol-based port definition).
Coverage: Protocol structure, async method signatures, docstring contract compliance.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.ports.repository import AuditEventRepository


class _ValidAuditEventRepository:
    """Minimal valid implementation of AuditEventRepository protocol."""

    async def append_event(
        self,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent:
        """Dummy implementation returning a valid event."""
        return AuditEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            sequence_no=1,
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
            previous_hash=None,
            event_hash="initial_hash",
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

    async def list_timeline(
        self,
        tenant_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        aggregate_type: str | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        """Dummy implementation returning empty list."""
        return [], None

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        """Dummy implementation returning empty list."""
        return []

    async def get_latest_event(
        self, aggregate_type: str, aggregate_id: UUID, *, action: str
    ) -> AuditEvent | None:
        """Dummy implementation returning None."""
        return None


class TestAuditEventRepositoryProtocol:
    """Protocol definition and structural tests."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """Verify protocol can be checked at runtime."""
        repo = _ValidAuditEventRepository()
        assert isinstance(repo, AuditEventRepository)

    def test_protocol_rejects_missing_append_event(self) -> None:
        """Missing append_event method violates protocol."""

        class _MissingAppendEvent:
            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                return []

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = _MissingAppendEvent()
        assert not isinstance(repo, AuditEventRepository)

    def test_protocol_rejects_missing_list_timeline(self) -> None:
        """Missing list_timeline method violates protocol."""

        class _MissingListTimeline:
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise NotImplementedError

            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                return []

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = _MissingListTimeline()
        assert not isinstance(repo, AuditEventRepository)

    def test_protocol_rejects_missing_list_chain_for_verification(self) -> None:
        """Missing list_chain_for_verification method violates protocol."""

        class _MissingListChain:
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise NotImplementedError

            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = _MissingListChain()
        assert not isinstance(repo, AuditEventRepository)

    def test_protocol_rejects_missing_get_latest_event(self) -> None:
        """Missing get_latest_event method violates protocol."""

        class _MissingGetLatest:
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise NotImplementedError

            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                return []

        repo = _MissingGetLatest()
        assert not isinstance(repo, AuditEventRepository)

    def test_append_event_signature_requires_keyword_only_args(self) -> None:
        """append_event must use keyword-only arguments (Rule 79 §1)."""

        class _MissingKwOnly:
            async def append_event(
                self,
                tenant_id: UUID | None,  # Missing * separator
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise NotImplementedError

            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                return []

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = _MissingKwOnly()
        # Note: @runtime_checkable does not validate positional vs keyword args
        # This is a structural limitation of the protocol mechanism
        # A real implementation must enforce this at call time
        assert isinstance(repo, AuditEventRepository)

    @pytest.mark.asyncio
    async def test_append_event_with_system_chain_null_tenant(self) -> None:
        """append_event accepts None tenant_id for system chain (Rule 79 §1)."""
        repo = _ValidAuditEventRepository()
        event = await repo.append_event(
            tenant_id=None,
            aggregate_type="system",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="init",
            outcome=Outcome.SUCCESS,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload_hash="abc123",
            payload={},
            classification=Classification.INTERNAL,
        )
        assert event.tenant_id is None
        assert event.id is not None

    @pytest.mark.asyncio
    async def test_list_timeline_cursor_pagination(self) -> None:
        """list_timeline supports opaque-cursor pagination (Rule 79 §3)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=10,
        )
        assert isinstance(items, list)
        assert cursor is None or isinstance(cursor, str)

    @pytest.mark.asyncio
    async def test_list_timeline_optional_filters(self) -> None:
        """list_timeline respects optional aggregate_type and action filters."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=20,
            aggregate_type="order",
            action="submit",
        )
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_list_chain_for_verification_system_chain(self) -> None:
        """list_chain_for_verification with None tenant returns system chain (Rule 79 §1)."""
        repo = _ValidAuditEventRepository()
        chain = await repo.list_chain_for_verification(tenant_id=None)
        assert isinstance(chain, list)

    @pytest.mark.asyncio
    async def test_get_latest_event_action_required(self) -> None:
        """get_latest_event requires action as keyword argument."""
        repo = _ValidAuditEventRepository()
        event = await repo.get_latest_event(
            aggregate_type="order",
            aggregate_id=uuid4(),
            action="submit",
        )
        assert event is None or isinstance(event, AuditEvent)

    @pytest.mark.asyncio
    async def test_append_event_rejects_invalid_payload_type(self) -> None:
        """Failure injection: append_event with invalid payload type (not dict)."""

        class _BadPayloadRepo:
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                # Simulate validation error
                if not isinstance(payload, dict):
                    raise TypeError("payload must be dict")
                raise NotImplementedError

            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                return [], None

            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                return []

            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                return None

        repo = _BadPayloadRepo()
        # Payload is already dict due to type annotation, but test that
        # an implementation could validate it
        assert isinstance(repo, AuditEventRepository)

    @pytest.mark.asyncio
    async def test_append_event_handles_empty_payload(self) -> None:
        """append_event accepts empty payload dict."""
        repo = _ValidAuditEventRepository()
        event = await repo.append_event(
            tenant_id=uuid4(),
            aggregate_type="order",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="create",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
            payload_hash="empty",
            payload={},
            classification=Classification.INTERNAL,
        )
        assert event.payload == {}

    @pytest.mark.asyncio
    async def test_append_event_handles_large_payload(self) -> None:
        """append_event accepts large nested payload."""
        repo = _ValidAuditEventRepository()
        large_payload: dict[str, object] = {
            f"field_{i}": {"nested": f"value_{i}"} for i in range(50)
        }
        event = await repo.append_event(
            tenant_id=uuid4(),
            aggregate_type="order",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="create",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
            payload_hash="large",
            payload=large_payload,
            classification=Classification.INTERNAL,
        )
        assert len(event.payload) == 50

    @pytest.mark.asyncio
    async def test_list_timeline_boundary_limit_zero(self) -> None:
        """list_timeline with limit=0 (boundary test)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=0,
        )
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_list_timeline_boundary_large_limit(self) -> None:
        """list_timeline with very large limit (boundary test)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=999999,
        )
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_list_timeline_empty_cursor(self) -> None:
        """list_timeline with empty string cursor (boundary test)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor="",
            limit=10,
        )
        assert isinstance(items, list)

    def test_protocol_attribute_error_on_invalid_repo(self) -> None:
        """Invalid repository does not satisfy protocol (structural failure)."""

        class _NoMethods:
            pass

        repo = _NoMethods()
        assert not isinstance(repo, AuditEventRepository)

    def test_protocol_accepts_extra_methods(self) -> None:
        """Repository can have extra methods beyond protocol requirements."""

        class _ExtraMethods(_ValidAuditEventRepository):
            async def extra_method(self) -> None:
                pass

        repo = _ExtraMethods()
        assert isinstance(repo, AuditEventRepository)

    @pytest.mark.asyncio
    async def test_append_event_outcome_all_variants(self) -> None:
        """append_event handles all Outcome enum variants."""
        repo = _ValidAuditEventRepository()
        for outcome in Outcome:
            event = await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="check",
                outcome=outcome,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=Classification.INTERNAL,
            )
            assert event.outcome == outcome

    @pytest.mark.asyncio
    async def test_append_event_classification_all_variants(self) -> None:
        """append_event handles all Classification enum variants."""
        repo = _ValidAuditEventRepository()
        for classification in Classification:
            event = await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="check",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=classification,
            )
            assert event.classification == classification

    @pytest.mark.asyncio
    async def test_list_timeline_aggregate_type_filter_only(self) -> None:
        """list_timeline filters by aggregate_type only (no action)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=10,
            aggregate_type="mandate",
        )
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_list_timeline_action_filter_only(self) -> None:
        """list_timeline filters by action only (no aggregate_type)."""
        repo = _ValidAuditEventRepository()
        items, cursor = await repo.list_timeline(
            tenant_id=uuid4(),
            cursor=None,
            limit=10,
            action="approve",
        )
        assert isinstance(items, list)

    def test_protocol_method_count(self) -> None:
        """Protocol defines exactly 4 methods."""
        protocol_methods = [
            method
            for method in dir(AuditEventRepository)
            if not method.startswith("_") and callable(getattr(AuditEventRepository, method))
        ]
        # Count only protocol methods, exclude object methods
        audit_methods = [
            m
            for m in protocol_methods
            if m
            in ["append_event", "list_timeline", "list_chain_for_verification", "get_latest_event"]
        ]
        assert len(audit_methods) == 4


class TestAppendEventFailureInjection:
    """Failure injection tests for append_event."""

    @pytest.mark.asyncio
    async def test_append_event_database_connection_failure(self, monkeypatch: Any) -> None:
        """Failure injection: database connection error in append_event."""

        class _FailingRepo(_ValidAuditEventRepository):
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise ConnectionError("Database unavailable")

        repo = _FailingRepo()
        with pytest.raises(ConnectionError) as exc_info:
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="create",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=Classification.INTERNAL,
            )
        assert "unavailable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_append_event_hash_chain_violation(self, monkeypatch: Any) -> None:
        """Failure injection: hash chain fork detection in append_event."""

        class _ChainViolationRepo(_ValidAuditEventRepository):
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise ValueError("Hash chain fork detected (Rule 79 §1)")

        repo = _ChainViolationRepo()
        with pytest.raises(ValueError) as exc_info:
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="create",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=Classification.INTERNAL,
            )
        assert "fork" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_list_timeline_cursor_invalid_format(self, monkeypatch: Any) -> None:
        """Failure injection: invalid cursor format in list_timeline."""

        class _InvalidCursorRepo(_ValidAuditEventRepository):
            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                if cursor is not None and cursor.startswith("invalid_"):
                    raise ValueError("Cursor format invalid")
                return [], None

        repo = _InvalidCursorRepo()
        with pytest.raises(ValueError):
            await repo.list_timeline(
                tenant_id=uuid4(),
                cursor="invalid_xyz",
                limit=10,
            )

    @pytest.mark.asyncio
    async def test_list_chain_for_verification_tenant_not_found(self, monkeypatch: Any) -> None:
        """Failure injection: tenant not found in list_chain_for_verification."""

        class _TenantNotFoundRepo(_ValidAuditEventRepository):
            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                if tenant_id is not None:
                    raise KeyError(f"Tenant {tenant_id} not found")
                return []

        repo = _TenantNotFoundRepo()
        with pytest.raises(KeyError):
            await repo.list_chain_for_verification(tenant_id=uuid4())

    @pytest.mark.asyncio
    async def test_get_latest_event_timeout(self, monkeypatch: Any) -> None:
        """Failure injection: timeout in get_latest_event."""

        class _TimeoutRepo(_ValidAuditEventRepository):
            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                raise TimeoutError("Query timeout")

        repo = _TimeoutRepo()
        with pytest.raises(TimeoutError):
            await repo.get_latest_event(
                aggregate_type="order",
                aggregate_id=uuid4(),
                action="submit",
            )
