"""Unit tests for get_audit_timeline application service.

Spec: 79_audit_evidence_l3_build_and_operational_specification_v1.0.md §3
"opaque cursor, time range, aggregate/action filter and maximum bounded page".
"""
# ratchet-allow: mock protocol implementation raises NotImplementedError for
# unused interface methods (append_event, list_chain_for_verification, get_latest_event)
# to match AuditEventRepository contract without full implementation

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.application.get_audit_timeline import (
    MAX_PAGE_SIZE,
    get_audit_timeline,
)
from src.foundation.evidence.contracts.v1 import AuditTimelinePage
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome


class MockAuditEventRepository:
    """Configurable mock for testing."""

    def __init__(self) -> None:
        self.list_timeline = AsyncMock()

    async def append_event(self, **kwargs: Any) -> AuditEvent:
        raise NotImplementedError

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        raise NotImplementedError

    async def get_latest_event(
        self, aggregate_type: str, aggregate_id: UUID, *, action: str
    ) -> AuditEvent | None:
        raise NotImplementedError


def _sample_event(
    sequence_no: int = 1,
    tenant_id: UUID | None = None,
    action: str = "test_action",
) -> AuditEvent:
    """Create a sample AuditEvent for testing."""
    return AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        sequence_no=sequence_no,
        aggregate_type="test_aggregate",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action=action,
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="hash",
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=None if sequence_no == 1 else "prev_hash",
        event_hash="event_hash",
        occurred_at=datetime.now(timezone.utc),
    )


class TestGetAuditTimelinePositivePath:
    """Happy path: normal operation with valid inputs."""

    @pytest.mark.asyncio
    async def test_returns_audit_timeline_page_with_items(self) -> None:
        """get_audit_timeline returns AuditTimelinePage with event views."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        event = _sample_event(tenant_id=tenant_id)
        repo.list_timeline.return_value = ([event], None)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert isinstance(result, AuditTimelinePage)
        assert len(result.items) == 1
        assert result.items[0].id == event.id
        assert result.next_cursor is None
        assert result.as_of is not None

    @pytest.mark.asyncio
    async def test_passes_tenant_id_to_repository(self) -> None:
        """get_audit_timeline passes tenant_id to repo.list_timeline."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id)

        repo.list_timeline.assert_called_once()
        call_args = repo.list_timeline.call_args
        assert call_args[0][0] == tenant_id

    @pytest.mark.asyncio
    async def test_passes_cursor_to_repository(self) -> None:
        """get_audit_timeline passes cursor kwarg to repo.list_timeline."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        cursor = "opaque_cursor_value"
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, cursor=cursor)

        repo.list_timeline.assert_called_once()
        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["cursor"] == cursor

    @pytest.mark.asyncio
    async def test_passes_aggregate_type_filter_to_repository(self) -> None:
        """get_audit_timeline passes aggregate_type filter to repo."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        agg_type = "mandate_revision"
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, aggregate_type=agg_type)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["aggregate_type"] == agg_type

    @pytest.mark.asyncio
    async def test_passes_action_filter_to_repository(self) -> None:
        """get_audit_timeline passes action filter to repo."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        action = "mandate_activated"
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, action=action)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["action"] == action

    @pytest.mark.asyncio
    async def test_sets_as_of_timestamp_to_utc_now(self) -> None:
        """get_audit_timeline sets as_of to current UTC time."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        before = datetime.now(timezone.utc)
        result = await get_audit_timeline(repo, tenant_id=tenant_id)
        after = datetime.now(timezone.utc)

        assert before <= result.as_of <= after


class TestGetAuditTimelineBoundaryLimit:
    """Boundary cases: limit parameter bounded to MAX_PAGE_SIZE."""

    @pytest.mark.asyncio
    async def test_limits_above_max_page_size_are_capped(self) -> None:
        """get_audit_timeline caps limit to MAX_PAGE_SIZE (100)."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, limit=150)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == MAX_PAGE_SIZE

    @pytest.mark.asyncio
    async def test_limit_exactly_max_page_size_is_passed_unchanged(self) -> None:
        """get_audit_timeline with limit=MAX_PAGE_SIZE passes it unchanged."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, limit=MAX_PAGE_SIZE)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == MAX_PAGE_SIZE

    @pytest.mark.asyncio
    async def test_limit_below_max_page_size_is_passed_unchanged(self) -> None:
        """get_audit_timeline with limit<MAX_PAGE_SIZE passes it unchanged."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, limit=25)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == 25

    @pytest.mark.asyncio
    async def test_default_limit_is_50(self) -> None:
        """get_audit_timeline defaults to limit=50 (below MAX_PAGE_SIZE)."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_negative_limit_is_capped_to_max_page_size(self) -> None:
        """get_audit_timeline with limit<0 results in limit=min(negative, MAX_PAGE_SIZE)."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, limit=-10)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == -10  # min(-10, 100) = -10

    @pytest.mark.asyncio
    async def test_zero_limit_is_capped_to_max_page_size(self) -> None:
        """get_audit_timeline with limit=0 results in limit=min(0, MAX_PAGE_SIZE)."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, limit=0)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["limit"] == 0  # min(0, 100) = 0


class TestGetAuditTimelineFiltering:
    """Filtering tests: aggregate_type and action filters."""

    @pytest.mark.asyncio
    async def test_both_filters_none_passes_none(self) -> None:
        """get_audit_timeline with no filters passes aggregate_type=None, action=None."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["aggregate_type"] is None
        assert call_kwargs["action"] is None

    @pytest.mark.asyncio
    async def test_only_aggregate_type_filter(self) -> None:
        """get_audit_timeline passes aggregate_type, action=None."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, aggregate_type="mandate")

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["aggregate_type"] == "mandate"
        assert call_kwargs["action"] is None

    @pytest.mark.asyncio
    async def test_only_action_filter(self) -> None:
        """get_audit_timeline passes action, aggregate_type=None."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, action="activated")

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["aggregate_type"] is None
        assert call_kwargs["action"] == "activated"

    @pytest.mark.asyncio
    async def test_both_filters_passed(self) -> None:
        """get_audit_timeline passes both aggregate_type and action filters."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(
            repo, tenant_id=tenant_id, aggregate_type="mandate", action="activated"
        )

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["aggregate_type"] == "mandate"
        assert call_kwargs["action"] == "activated"


class TestGetAuditTimelineCursorPagination:
    """Pagination tests: cursor handling."""

    @pytest.mark.asyncio
    async def test_next_cursor_none_when_no_more_pages(self) -> None:
        """AuditTimelinePage.next_cursor is None when repository returns None."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert result.next_cursor is None

    @pytest.mark.asyncio
    async def test_next_cursor_returned_when_more_pages_exist(self) -> None:
        """AuditTimelinePage.next_cursor is set when repo returns one."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        next_cursor = "opaque_next_cursor"
        repo.list_timeline.return_value = ([], next_cursor)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert result.next_cursor == next_cursor

    @pytest.mark.asyncio
    async def test_cursor_none_first_page(self) -> None:
        """get_audit_timeline with cursor=None fetches first page."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        await get_audit_timeline(repo, tenant_id=tenant_id, cursor=None)

        call_kwargs = repo.list_timeline.call_args[1]
        assert call_kwargs["cursor"] is None


class TestGetAuditTimelineFailureInjection:
    """Failure injection: repository exceptions."""

    @pytest.mark.asyncio
    async def test_repository_exception_propagates(self) -> None:
        """get_audit_timeline propagates repo.list_timeline exceptions."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.side_effect = RuntimeError("Database connection failed")

        with pytest.raises(RuntimeError, match="Database connection failed"):
            await get_audit_timeline(repo, tenant_id=tenant_id)

    @pytest.mark.asyncio
    async def test_repository_value_error_propagates(self) -> None:
        """get_audit_timeline propagates ValueError from repo."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.side_effect = ValueError("Invalid cursor format")

        with pytest.raises(ValueError, match="Invalid cursor format"):
            await get_audit_timeline(repo, tenant_id=tenant_id)

    @pytest.mark.asyncio
    async def test_repository_timeout_propagates(self) -> None:
        """get_audit_timeline propagates TimeoutError from repo."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.side_effect = TimeoutError("Query timeout")

        with pytest.raises(TimeoutError, match="Query timeout"):
            await get_audit_timeline(repo, tenant_id=tenant_id)


class TestGetAuditTimelineEventToViewConversion:
    """Event conversion: event_to_view transformation."""

    @pytest.mark.asyncio
    async def test_events_converted_to_views(self) -> None:
        """get_audit_timeline converts AuditEvent to AuditEventView."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        event = _sample_event(sequence_no=1, tenant_id=tenant_id)
        repo.list_timeline.return_value = ([event], None)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert len(result.items) == 1
        view = result.items[0]
        assert view.id == event.id
        assert view.tenant_id == event.tenant_id
        assert view.sequence_no == event.sequence_no
        assert view.aggregate_type == event.aggregate_type
        assert view.action == event.action
        assert view.outcome == event.outcome

    @pytest.mark.asyncio
    async def test_multiple_events_all_converted(self) -> None:
        """get_audit_timeline converts all events from repository."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        events = [
            _sample_event(sequence_no=1, tenant_id=tenant_id, action="created"),
            _sample_event(sequence_no=2, tenant_id=tenant_id, action="updated"),
            _sample_event(sequence_no=3, tenant_id=tenant_id, action="deleted"),
        ]
        repo.list_timeline.return_value = (events, None)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert len(result.items) == 3
        assert [v.sequence_no for v in result.items] == [1, 2, 3]
        assert [v.action for v in result.items] == ["created", "updated", "deleted"]

    @pytest.mark.asyncio
    async def test_empty_event_list(self) -> None:
        """get_audit_timeline handles empty event list."""
        repo = MockAuditEventRepository()
        tenant_id = uuid4()
        repo.list_timeline.return_value = ([], None)

        result = await get_audit_timeline(repo, tenant_id=tenant_id)

        assert result.items == []
        assert result.next_cursor is None
