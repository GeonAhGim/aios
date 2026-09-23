"""RD-6 -- `application/ingest_job.py` tests (fake ports, no database).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-6.
DoD: interrupt/resume, rate-limit backoff, and coverage-gap marking are all
proven against adversarial fakes -- `_FakeStore` reproduces RD-4's
`(tenant_id, source_id, external_id)` ON CONFLICT DO NOTHING semantics so
the idempotent-retry-after-crash guarantee is exercised without Postgres.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.http_policy import RetryPolicy
from src.foundation.research_data.application.ingest_job import (
    CoverageGap,
    IngestPage,
    ingest_job,
)
from src.foundation.research_data.contracts.v1 import ResearchItem

_TENANT = uuid4()
_SOURCE_ID = "OPENDART"
_SPAN = "2026-03"
_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


def _item(external_id: str, *, item_id: UUID | None = None) -> ResearchItem:
    return ResearchItem(
        item_id=item_id or uuid4(),
        source_id=_SOURCE_ID,
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=("005930",),
        title=f"filing {external_id}",
        body_ref=None,
        url=f"https://dart.fss.or.kr/{external_id}",
        language="ko",
        hash=f"hash-{external_id}",
        revision_of=None,
    )


class _FakeStore:
    """Mirrors `PostgresResearchRepository.append_item`'s idempotency
    contract: the first writer for a given `(tenant_id, source_id,
    external_id)` wins, later calls return the same `item_id` and never
    add a second row."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str, str], UUID] = {}
        self.append_calls = 0

    async def append_item(self, tenant_id: UUID, item: ResearchItem, *, external_id: str) -> UUID:
        self.append_calls += 1
        key = (tenant_id, item.source_id, external_id)
        existing = self.rows.get(key)
        if existing is not None:
            return existing
        self.rows[key] = item.item_id
        return item.item_id


def _noop_lock(_key: str) -> AbstractAsyncContextManager[None]:
    @asynccontextmanager
    async def _acquire() -> AsyncIterator[None]:
        yield

    return _acquire()


def _tracking_lock(
    active: list[str], max_concurrent: list[int]
) -> Callable[[str], AbstractAsyncContextManager[None]]:
    """Fakes `pg_advisory_xact_lock`'s mutual-exclusion behavior (a second
    acquirer for the same key blocks until the first releases) with a
    real `asyncio.Lock` per key -- `_tracking_lock` itself would only
    record overlap, not prevent it, so the lock is load-bearing here, not
    decorative."""
    locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def _acquire(key: str) -> AsyncIterator[None]:
        held = locks.setdefault(key, asyncio.Lock())
        async with held:
            active.append(key)
            max_concurrent.append(len(active))
            try:
                yield
            finally:
                active.remove(key)

    return _acquire


async def test_valid_page_is_stored_and_cursor_advances() -> None:
    store = _FakeStore()
    items = ((_item("ext-1"), "ext-1"), (_item("ext-2"), "ext-2"))
    page = IngestPage(items=items, next_cursor="c2")

    async def fetch_page(cursor: str | None) -> IngestPage:
        assert cursor is None
        return page

    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
    )

    assert len(result.stored_item_ids) == 2
    assert result.cursor == "c2"
    assert result.gap is None
    assert len(store.rows) == 2


async def test_resume_cursor_continues_from_last_page() -> None:
    store = _FakeStore()
    seen_cursors: list[str | None] = []

    async def fetch_page(cursor: str | None) -> IngestPage:
        seen_cursors.append(cursor)
        return IngestPage(items=((_item("ext-3"), "ext-3"),), next_cursor="c3")

    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
        resume_cursor="c2",
    )

    assert seen_cursors == ["c2"]
    assert result.cursor == "c3"


async def test_empty_page_returns_no_gap_and_no_stored_items() -> None:
    store = _FakeStore()

    async def fetch_page(_cursor: str | None) -> IngestPage:
        return IngestPage(items=(), next_cursor=None)

    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
    )

    assert result.stored_item_ids == ()
    assert result.gap is None
    assert result.cursor is None


async def test_retryable_error_backs_off_with_expected_delay_then_succeeds() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    calls = 0

    async def fetch_page(_cursor: str | None) -> IngestPage:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExchangeError(ExchangeErrorKind.RATE_LIMITED, retry_after_sec=2.5)
        return IngestPage(items=((_item("ext-4"), "ext-4"),), next_cursor=None)

    store = _FakeStore()
    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
        sleep=fake_sleep,
        rng=lambda: 0.5,
    )

    # retry_after_sec is server-declared and must be honored verbatim
    # (backoff_delay's own contract) -- not overridden by our jitter formula.
    assert delays == [2.5]
    assert calls == 2
    assert len(result.stored_item_ids) == 1
    assert result.gap is None


async def test_non_retryable_error_records_gap_without_sleeping() -> None:
    slept = False

    async def fake_sleep(_delay: float) -> None:
        nonlocal slept
        slept = True

    async def fetch_page(_cursor: str | None) -> IngestPage:
        raise ExchangeError(ExchangeErrorKind.AUTH, message="invalid api key")

    store = _FakeStore()
    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
        sleep=fake_sleep,
        resume_cursor="c5",
    )

    assert slept is False
    assert result.stored_item_ids == ()
    assert result.cursor == "c5"
    assert isinstance(result.gap, CoverageGap)
    assert result.gap.source_id == _SOURCE_ID
    assert result.gap.span == _SPAN
    assert result.gap.cursor == "c5"
    assert store.append_calls == 0


async def test_retry_budget_exhausted_records_gap_instead_of_raising() -> None:
    """Failure-injection: the source is permanently rate-limited (every
    call raises a retryable error). `ingest_job` must exhaust the retry
    budget and report a `CoverageGap` -- never raise out of the job, and
    never drop the page silently (§6)."""

    async def fetch_page(_cursor: str | None) -> IngestPage:
        raise ExchangeError(ExchangeErrorKind.RATE_LIMITED, retry_after_sec=0.0)

    store = _FakeStore()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    policy = RetryPolicy(max_attempts=3)
    result = await ingest_job(
        tenant_id=_TENANT,
        source_id=_SOURCE_ID,
        span=_SPAN,
        fetch_page=fetch_page,
        store=store,
        lock=_noop_lock,
        retry_policy=policy,
        sleep=fake_sleep,
    )

    assert result.gap is not None
    assert result.stored_item_ids == ()
    # attempts 1..max_attempts-1 sleep, the max_attempts-th raises without sleeping again.
    assert len(sleeps) == policy.max_attempts - 1
    assert store.append_calls == 0


async def test_idempotent_retry_after_crash_inserts_no_duplicate_rows() -> None:
    """Red-gate reproduction: without RD-4's ON CONFLICT DO NOTHING
    semantics (mirrored by `_FakeStore`), replaying the same page after a
    mid-batch crash would insert a second row for `ext-6`. This proves the
    guarantee holds -- exactly one row survives two full runs of the same
    page."""
    store = _FakeStore()
    page = IngestPage(items=((_item("ext-6"), "ext-6"),), next_cursor="c7")

    async def fetch_page(_cursor: str | None) -> IngestPage:
        return page

    first = await ingest_job(
        tenant_id=_TENANT, source_id=_SOURCE_ID, span=_SPAN,
        fetch_page=fetch_page, store=store, lock=_noop_lock,
    )
    # Simulate a crash after storage but before the caller persisted the new
    # cursor -- the caller retries with the *same* (stale) resume_cursor and
    # the source replays the identical page.
    second = await ingest_job(
        tenant_id=_TENANT, source_id=_SOURCE_ID, span=_SPAN,
        fetch_page=fetch_page, store=store, lock=_noop_lock,
    )

    assert first.stored_item_ids == second.stored_item_ids
    assert len(store.rows) == 1
    assert store.append_calls == 2


async def test_concurrent_jobs_same_lock_key_are_serialized() -> None:
    """`(source_id, span)` advisory lock (§5) -- two concurrent `ingest_job`
    calls for the same key must never hold the lock at the same time."""
    active: list[str] = []
    max_concurrent: list[int] = []
    lock = _tracking_lock(active, max_concurrent)
    store = _FakeStore()

    async def slow_fetch(_cursor: str | None) -> IngestPage:
        await asyncio.sleep(0)  # yield control so a concurrent call could interleave
        return IngestPage(items=((_item("ext-7"), "ext-7"),), next_cursor=None)

    await asyncio.gather(
        ingest_job(
            tenant_id=_TENANT, source_id=_SOURCE_ID, span=_SPAN,
            fetch_page=slow_fetch, store=store, lock=lock,
        ),
        ingest_job(
            tenant_id=_TENANT, source_id=_SOURCE_ID, span=_SPAN,
            fetch_page=slow_fetch, store=store, lock=lock,
        ),
    )

    assert max(max_concurrent) == 1


async def test_page_processing_throughput_bounded() -> None:
    """Numeric performance assertion (throughput): storing 1,000 items
    through `ingest_job` must not regress into superlinear behavior.
    2 seconds for 1,000 fake (in-memory, no I/O) items is generous -- this
    guards against an accidental O(n^2) construction, not a tight SLO
    (no ingest-job-specific budget row exists in ADR-2026-09-09-C's table;
    §7's "collection job lag <= source publication + 5 min" is an
    end-to-end SLO, not this function's own budget)."""
    items = tuple((_item(f"ext-{i}"), f"ext-{i}") for i in range(1000))
    page = IngestPage(items=items, next_cursor=None)

    async def fetch_page(_cursor: str | None) -> IngestPage:
        return page

    store = _FakeStore()
    started = time.monotonic()
    result = await ingest_job(
        tenant_id=_TENANT, source_id=_SOURCE_ID, span=_SPAN,
        fetch_page=fetch_page, store=store, lock=_noop_lock,
    )
    elapsed = time.monotonic() - started

    assert len(result.stored_item_ids) == 1000
    assert elapsed < 2.0
