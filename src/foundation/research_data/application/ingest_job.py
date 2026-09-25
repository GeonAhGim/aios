"""RD-6 -- application/ingest_job.py: idempotent WORM ingest for research items.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.4
`application/{ingest_job,backfill_job}.py`, §5 (`(source_id, span)` advisory
lock, conditional cursor update, resumable), §6 (source outage/rate limit ->
backoff, coverage gap marked -- never a silent miss), §9 RD-6 ("interrupt
and resume, rate-limit backoff, gap marking"). `backfill_job.py` (historical
backfill scheduling) is a separate leaf -- see task note "task-2707 후신,
backfill_job.py는 후속 리프로 분리". This file only owns the incremental
collector step: fetch one page from a source, then persist each entry
idempotently.

Idempotency: persistence goes through RD-4's `PostgresResearchRepository
.append_item`, whose `(tenant_id, source_id, external_id)` unique
constraint with `ON CONFLICT DO NOTHING` is the actual idempotency
boundary (`adapters/postgres_repository.py` docstring) -- `ingest_job`
relies on that constraint rather than re-checking existence itself, so a
crash-and-retry of the same page never produces a duplicate row. This file
depends on `append_item`'s signature only (`ResearchItemStore` Protocol
below), not the concrete class, so unit tests use an in-memory fake without
a database.

Rate-limit backoff reuses L4-11's `RetryPolicy`/`backoff_delay`
(`src/exchanges/common/http_policy.py`) instead of a new formula -- the
same reuse DC-11 `BaseProviderAdapter.call_with_retry` already applies for
market-data collectors (`retryable`/`retry_after_sec` off `ExchangeError`).
Time and randomness are fully injected (`sleep`/`rng`), so tests assert
exact delays without a real wait (task-423 d3227c9 pattern).

Advisory lock: `(source_id, span)` is the lock key (§5), acquired through
the injected `LockAcquirer` rather than a hardcoded `asyncpg.Pool` --
`pg_advisory_lock()` below is the production implementation
(`pg_advisory_xact_lock`, the same primitive LA-16 `ingest_ticks.py` uses
to serialize concurrent batches for one key); tests inject a fake lock to
verify serialization without a real database.

Resume: the caller supplies `resume_cursor` -- the `cursor` field of a
previous `IngestJobResult`, persisted by the caller between runs (this leaf
has no cursor storage of its own; only `ingest_job.py` is in scope here --
`backfill_job.py` owns the persisted schedule). Passing that value back
resumes exactly where the prior run stopped; the job never re-derives a
starting point from the stored data.

Gap marking: a source failure that is not retryable, or whose retry budget
is exhausted, does not raise out of `ingest_job` and is never dropped
silently (§6, "조용한 결측 금지") -- it comes back as `IngestJobResult.gap`
so the caller (backfill retry, coverage dashboard) can act on it.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.exchanges.common.error_taxonomy import ExchangeError
from src.exchanges.common.http_policy import RetryPolicy, backoff_delay
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = [
    "IngestPage",
    "PageFetcher",
    "ResearchItemStore",
    "LockAcquirer",
    "CoverageGap",
    "IngestJobResult",
    "pg_advisory_lock",
    "ingest_job",
]


@dataclass(frozen=True)
class IngestPage:
    """One page of raw entries from a source. `items` pairs each already
    normalized `ResearchItem` with its source-native `external_id` (RD-4's
    idempotency key component) -- normalization itself (RD-10..15 source
    adapters) is out of scope for this leaf; the caller supplies
    already-normalized items. `next_cursor` is `None` once the source has
    no more pages."""

    items: tuple[tuple[ResearchItem, str], ...]
    next_cursor: str | None


PageFetcher = Callable[[str | None], Awaitable[IngestPage]]


@runtime_checkable
class ResearchItemStore(Protocol):
    async def append_item(
        self, tenant_id: UUID, item: ResearchItem, *, external_id: str
    ) -> UUID:
        """Matches `PostgresResearchRepository.append_item` (RD-4) exactly, so
        a real repository instance satisfies this Protocol unmodified;
        unit tests pass an in-memory fake instead."""
        ...


LockAcquirer = Callable[[str], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class CoverageGap:
    """A page that could not be fetched even after the retry budget was
    exhausted (or was rejected outright as non-retryable). Reported instead
    of silently skipped (§6)."""

    source_id: str
    span: str
    cursor: str | None
    reason: str


@dataclass(frozen=True)
class IngestJobResult:
    source_id: str
    span: str
    tenant_id: UUID
    stored_item_ids: tuple[UUID, ...]
    cursor: str | None
    gap: CoverageGap | None


def pg_advisory_lock(pool: asyncpg.Pool) -> LockAcquirer:
    """Production `LockAcquirer`: `pg_advisory_xact_lock` scoped to a
    dedicated connection/transaction for the lock's lifetime (same
    primitive as LA-16 `ingest_ticks.py`). Blocks a concurrent
    `ingest_job` call for the same `(source_id, span)` key until this one
    releases the lock by exiting the context manager."""

    @asynccontextmanager
    async def _acquire(key: str) -> AsyncIterator[None]:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            yield

    return _acquire


async def _fetch_with_backoff(
    fetch_page: PageFetcher,
    cursor: str | None,
    *,
    retry_policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]],
    rng: Callable[[], float],
) -> IngestPage:
    attempt = 0
    while True:
        try:
            return await fetch_page(cursor)
        except ExchangeError as exc:
            attempt += 1
            if not exc.retryable or attempt >= retry_policy.max_attempts:
                raise
            delay = backoff_delay(retry_policy, attempt, exc.retry_after_sec, rng)
            await sleep(delay)


async def ingest_job(
    *,
    tenant_id: UUID,
    source_id: str,
    span: str,
    fetch_page: PageFetcher,
    store: ResearchItemStore,
    lock: LockAcquirer,
    resume_cursor: str | None = None,
    retry_policy: RetryPolicy | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> IngestJobResult:
    """Fetch one page from `source_id` (resuming from `resume_cursor` if
    given) and persist every entry idempotently. Never raises `ExchangeError`
    -- a source failure becomes `IngestJobResult.gap` instead (§6)."""
    del clock  # reserved for future SLO instrumentation (§7); unused by this leaf's logic
    policy = retry_policy or RetryPolicy()

    async with lock(f"{source_id}:{span}"):
        try:
            page = await _fetch_with_backoff(
                fetch_page, resume_cursor, retry_policy=policy, sleep=sleep, rng=rng
            )
        except ExchangeError as exc:
            gap = CoverageGap(
                source_id=source_id, span=span, cursor=resume_cursor, reason=str(exc)
            )
            return IngestJobResult(
                source_id=source_id,
                span=span,
                tenant_id=tenant_id,
                stored_item_ids=(),
                cursor=resume_cursor,
                gap=gap,
            )

        stored: list[UUID] = []
        for item, external_id in page.items:
            item_id = await store.append_item(tenant_id, item, external_id=external_id)
            stored.append(item_id)

        return IngestJobResult(
            source_id=source_id,
            span=span,
            tenant_id=tenant_id,
            stored_item_ids=tuple(stored),
            cursor=page.next_cursor,
            gap=None,
        )
