"""RD-6 -- `application/ingest_job.py` tests (fake ports, no database).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-6.
DoD: interrupt/resume, rate-limit backoff, and coverage-gap marking are all
proven against adversarial fakes -- `_FakeStore` reproduces RD-4's
`(tenant_id, source_id, external_id)` ON CONFLICT DO NOTHING semantics so
the idempotent-retry-after-crash guarantee is exercised without Postgres.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.foundation.research_data.application.ingest_job import (
    IngestPage,
    ingest_job,
)
from src.foundation.research_data.contracts.v1 import ResearchItem
from tests.conftest import PerfBudget

_TENANT = uuid4()
_SOURCE_ID = "OPENDART"
_SPAN = "2026-03"
_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


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


@pytest.mark.perf
async def test_page_processing_throughput_gate_red_with_io_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현 — 성능 단언이 단순한 통과가 아니라 실제로 지연을
    포착하는지 검증한다. `store.append_item`에 `delayed_async`로 IO 지연
    (0.005s × 1000 items = 5s)을 주입하면 같은 wall_ms < 2000ms 단언이
    AssertionError로 적색이 된다.

    이 테스트가 없으면 "wall_ms < 2000ms" 단언이 항상 녹색일 수 있다(예:
    budget가 너무 느슨하거나 단언이 누락된 경우). 지연을 인위적으로
    만들어 단언이 적색으로 전환되는 것을 확인함으로써, 이 단언이
    실제 성능 회귀를 감지할 수 있는 게이트임을 증명한다.

    DoD 요구: failure-injection 1, gate-red reproduction 1.
    """
    from tests.support.perf_inject import delayed_async

    items = tuple((_item(f"ext-{i}"), f"ext-{i}") for i in range(1000))
    page = IngestPage(items=items, next_cursor=None)

    async def fetch_page(_cursor: str | None) -> IngestPage:
        return page

    store = _FakeStore()
    # 1000 items × 0.005s = 5s — 예산(2s)의 2.5배 이상
    monkeypatch.setattr(store, "append_item", delayed_async(store.append_item, 0.005))

    async def run_ingest() -> None:
        await ingest_job(
            tenant_id=_TENANT,
            source_id=_SOURCE_ID,
            span=_SPAN,
            fetch_page=fetch_page,
            store=store,
            lock=_noop_lock,
        )

    samples = await perf_budget.samples_async(run_ingest, n=3)
    best = min(samples, key=lambda s: s.wall_ms)

    # 지연 주입 후 wall_ms가 2000ms를 넘어야 하므로, 넘지 않으면 테스트 설정이 잘못됨
    assert best.wall_ms >= 2000.0, (
        f"게이트 적색 재현 실패: wall_ms {best.wall_ms:.1f}ms가 2000ms 미만 — "
        "지연 주입이 충분히 작거나 단언이 느슨함"
    )
