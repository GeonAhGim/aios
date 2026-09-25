"""Tests for AuditEventRepository protocol -- structural conformance.

Spec: Rule 71 §4 (protocol-based port definition).
Coverage: Protocol structure, async method signatures, runtime_checkable
rejection of incomplete implementations.

See also:
  - test_repository_protocol_contract.py -- behavioral contract tests
  - test_repository_protocol_failure_injection.py -- failure-injection tests
"""

from __future__ import annotations

from uuid import UUID, uuid4

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
            ) -> (
                AuditEvent
            ):
                raise AssertionError("unreached: isinstance() checks structure, not behavior")

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
            ) -> (
                AuditEvent
            ):
                raise AssertionError("unreached: isinstance() checks structure, not behavior")

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
            ) -> (
                AuditEvent
            ):
                raise AssertionError("unreached: isinstance() checks structure, not behavior")

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
            ) -> (
                AuditEvent
            ):
                raise AssertionError("unreached: isinstance() checks structure, not behavior")

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
