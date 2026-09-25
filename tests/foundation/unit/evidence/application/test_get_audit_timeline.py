"""GetAuditTimeline 쿼리 — repo를 페이크로 대체한 순수 오케스트레이션
단위테스트. get_audit_timeline.py 실행 가능 statement 커버리지 0% -> 보강.

Spec: AIOSproject #79 §3."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.application.get_audit_timeline import (
    MAX_PAGE_SIZE,
    get_audit_timeline,
)
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


class FakeAuditEventRepository:
    """In-memory stand-in for AuditEventRepository — records the effective
    `limit` passed by the caller so tests can assert page-size clamping."""

    def __init__(
        self,
        *,
        events: list[AuditEvent] | None = None,
        next_cursor: str | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.next_cursor = next_cursor
        self.fail_with = fail_with
        self.calls: list[dict[str, object]] = []

    async def list_timeline(
        self,
        tenant_id,
        *,
        cursor,
        limit,
        aggregate_type=None,
        action=None,
    ):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "cursor": cursor,
                "limit": limit,
                "aggregate_type": aggregate_type,
                "action": action,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        return self.events, self.next_cursor


def _event(**overrides) -> AuditEvent:
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="activate",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="abc",
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=None,
        event_hash="hash-1",
        occurred_at=NOW,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


async def test_returns_mapped_page_with_next_cursor():
    tenant_id = uuid4()
    repo = FakeAuditEventRepository(events=[_event(tenant_id=tenant_id)], next_cursor="cur-2")

    page = await get_audit_timeline(repo, tenant_id=tenant_id, limit=10)

    assert len(page.items) == 1
    assert page.items[0].tenant_id == tenant_id
    assert page.next_cursor == "cur-2"
    assert page.as_of.tzinfo is not None


async def test_empty_result_has_no_next_cursor():
    repo = FakeAuditEventRepository(events=[], next_cursor=None)

    page = await get_audit_timeline(repo, tenant_id=uuid4())

    assert page.items == []
    assert page.next_cursor is None


async def test_filters_and_cursor_forwarded_to_repo():
    repo = FakeAuditEventRepository(events=[])
    tenant_id = uuid4()

    await get_audit_timeline(
        repo,
        tenant_id=tenant_id,
        cursor="cur-1",
        limit=25,
        aggregate_type="mandate",
        action="activate",
    )

    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["tenant_id"] == tenant_id
    assert call["cursor"] == "cur-1"
    assert call["limit"] == 25
    assert call["aggregate_type"] == "mandate"
    assert call["action"] == "activate"


async def test_limit_above_max_is_clamped():
    repo = FakeAuditEventRepository(events=[])

    await get_audit_timeline(repo, tenant_id=uuid4(), limit=MAX_PAGE_SIZE + 500)

    assert repo.calls[0]["limit"] == MAX_PAGE_SIZE


# --- Negative tests (>=3) ---------------------------------------------------


async def test_limit_zero_is_forwarded_unmodified():
    repo = FakeAuditEventRepository(events=[])

    await get_audit_timeline(repo, tenant_id=uuid4(), limit=0)

    assert repo.calls[0]["limit"] == 0


async def test_negative_limit_is_forwarded_unmodified():
    repo = FakeAuditEventRepository(events=[])

    await get_audit_timeline(repo, tenant_id=uuid4(), limit=-1)

    assert repo.calls[0]["limit"] == -1


async def test_unknown_aggregate_type_filter_yields_empty_page():
    repo = FakeAuditEventRepository(events=[])

    page = await get_audit_timeline(repo, tenant_id=uuid4(), aggregate_type="no-such-type")

    assert page.items == []
    assert repo.calls[0]["aggregate_type"] == "no-such-type"


# --- Failure injection -------------------------------------------------------


async def test_repo_failure_propagates_without_being_swallowed():
    repo = FakeAuditEventRepository(fail_with=RuntimeError("db connection lost"))

    with pytest.raises(RuntimeError, match="db connection lost"):
        await get_audit_timeline(repo, tenant_id=uuid4())

    assert len(repo.calls) == 1


# --- Performance assertion (marker required — task-4674 QA note) ------------


@pytest.mark.perf
async def test_get_audit_timeline_orchestration_throughput():
    """Pure orchestration overhead (limit clamp + view mapping, repo I/O
    excluded via in-memory fake) for 500 sequential calls stays well under a
    1s budget — guards against an accidental regression on the read path."""
    repo = FakeAuditEventRepository(events=[_event()])
    tenant_id = uuid4()

    start = time.perf_counter()
    for _ in range(500):
        await get_audit_timeline(repo, tenant_id=tenant_id)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert len(repo.calls) == 500
