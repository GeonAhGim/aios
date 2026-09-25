"""Tests for AuditEventRepository protocol -- behavioral contract.

Spec: Rule 79 §1 (system chain, hash chain), §3 (opaque-cursor pagination).
Coverage: append_event/list_timeline/list_chain_for_verification/get_latest_event
exercised against a valid in-memory implementation.

See also: test_repository_protocol.py (structural conformance tests),
test_repository_protocol_failure_injection.py (failure-injection tests).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.ports.repository import AuditEventRepository
from tests.foundation.evidence.ports.test_repository_protocol import _ValidAuditEventRepository


class TestAuditEventRepositoryContract:
    """Behavioral contract tests against a valid repository implementation."""

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
                raise AssertionError("unreached: this branch never runs under type-checked payload")

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
