"""EM-6 `application/route_order.py` -- mocked failure injection + perf
assertion + gate-red repro.

DEEPEN(task-3117, ADR-2026-09-09-C D2 floor for EM axis): the existing
`tests/foundation/integration/ems/test_route_order.py` only exercises real
Postgres (a real `UNIQUE (order_id)` race, a real connection pool). A real
connection drop or a `UniqueViolationError` racing the app-level
`ON CONFLICT DO NOTHING` cannot be injected deterministically against a
live database -- this file isolates the application layer behind an
in-memory `FakeRouteDecisionRepository` so those failures can be forced on
demand (same reasoning as `tests/foundation/unit/charting/
test_indicator_template_application.py`, task-3090). EM is not itself the
compliance/risk safety axis (ADR-2026-09-09-C axis list), so only the D2
floor applies -- D3 (adversarial/replay/multi-instance) is out of scope
for this leaf.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.ems.application.route_order import route_order
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.domain.route.venue_scoring import (
    DEFAULT_VENUE_SCORE_WEIGHTS,
    VenueCandidate,
    VenueScoreWeights,
)
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord


@dataclass
class FakeRouteDecisionRepository:
    """In-memory stand-in for `RouteDecisionRepository`. `insert_exc`, when
    set, is raised on every `insert_or_get` call *before* anything is
    recorded -- simulating a save-path failure (dropped connection,
    constraint violation) instead of a successful write."""

    records: dict[UUID, RouteDecisionRecord] = field(default_factory=dict)
    insert_exc: Exception | None = None
    insert_calls: int = 0

    async def insert_or_get(
        self,
        *,
        order_id: UUID,
        decision: RouteDecision,
        candidates_snapshot: list[dict[str, object]],
        score_snapshot: list[dict[str, object]],
        weights_snapshot: dict[str, object],
        decided_at: datetime,
    ) -> RouteDecisionRecord:
        self.insert_calls += 1
        if self.insert_exc is not None:
            raise self.insert_exc
        if order_id in self.records:
            return self.records[order_id]
        record = RouteDecisionRecord(
            decision_id=uuid4(),
            order_id=order_id,
            decision=decision,
            candidates_snapshot=candidates_snapshot,
            score_snapshot=score_snapshot,
            weights_snapshot=weights_snapshot,
            decided_at=decided_at,
            created_at=datetime.now(timezone.utc),
        )
        self.records[order_id] = record
        return record

    async def get_by_order_id(self, order_id: UUID) -> RouteDecisionRecord | None:
        return self.records.get(order_id)


def _candidates(n: int) -> list[VenueCandidate]:
    return [
        VenueCandidate(venue=f"V{i:04d}", fee_bps=Decimal(i % 50), liquidity_score=Decimal("0.5"))
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_route_order_rejects_duplicate_venue_candidates_no_repo_call() -> None:
    """Duplicate venue codes are EM-5's `rank_venues` rejection (`ValueError`),
    not EM-6's -- but EM-6 must still be fail-closed about it: the repository
    must never be called for a routing attempt that was rejected before a
    winner was chosen."""
    repo = FakeRouteDecisionRepository()
    candidates = [
        VenueCandidate(venue="DUP", fee_bps=Decimal("1"), liquidity_score=Decimal("0.5")),
        VenueCandidate(venue="DUP", fee_bps=Decimal("2"), liquidity_score=Decimal("0.1")),
    ]

    with pytest.raises(ValueError, match="duplicate venue codes"):
        await route_order(
            repo, order_id=uuid4(), candidates=candidates, decided_at=datetime.now(timezone.utc)
        )

    assert repo.insert_calls == 0


async def test_route_order_propagates_repository_connection_drop_fail_closed() -> None:
    """DB save-path failure injection (connection drop) -- `route_order`
    must not swallow or retry a dropped connection, only propagate it, so
    the caller (and not this leaf) decides whether to retry."""
    repo = FakeRouteDecisionRepository(insert_exc=ConnectionResetError("simulated connection drop"))
    order_id = uuid4()

    with pytest.raises(ConnectionResetError):
        await route_order(
            repo,
            order_id=order_id,
            candidates=_candidates(2),
            decided_at=datetime.now(timezone.utc),
        )

    assert await repo.get_by_order_id(order_id) is None


async def test_route_order_propagates_repository_unique_constraint_violation() -> None:
    """DB save-path failure injection (constraint violation) -- if the
    `ON CONFLICT (order_id) DO NOTHING` re-select race in
    `PostgresRouteDecisionRepository.insert_or_get` (adapters/
    postgres_route_decision_repository.py) ever regressed into a bare
    INSERT, a concurrent duplicate would surface as a real
    `UniqueViolationError` instead of the current dedupe-and-return-existing
    behaviour. This proves `route_order` does not mask that failure mode --
    it fails closed on it rather than swallowing it as if it were a
    successful idempotent return."""
    repo = FakeRouteDecisionRepository(
        insert_exc=asyncpg.exceptions.UniqueViolationError(
            'duplicate key value violates unique constraint "route_decisions_order_id_key"'
        )
    )
    order_id = uuid4()

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await route_order(
            repo,
            order_id=order_id,
            candidates=_candidates(2),
            decided_at=datetime.now(timezone.utc),
        )

    assert await repo.get_by_order_id(order_id) is None


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves EM-5's duplicate-venue guard is load-bearing
# for EM-6's persisted evidence, via a real before/after within one test.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_duplicate_venue_guard_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id = uuid4()
    candidates = [
        VenueCandidate(venue="DUP", fee_bps=Decimal("1"), liquidity_score=Decimal("0.5")),
        VenueCandidate(venue="DUP", fee_bps=Decimal("2"), liquidity_score=Decimal("0.1")),
    ]
    repo = FakeRouteDecisionRepository()

    # green: guard active -- duplicate venues are rejected, nothing persisted.
    with pytest.raises(ValueError):
        await route_order(
            repo, order_id=order_id, candidates=candidates, decided_at=datetime.now(timezone.utc)
        )
    assert repo.insert_calls == 0

    # red repro: neutralize exactly the duplicate-venue check EM-5's
    # `rank_venues` performs. EM-6 does not re-check this itself (it
    # delegates ranking to EM-5, per this module's docstring) -- so a
    # regression that deletes that check in venue_scoring.py would silently
    # let EM-6 persist a `route_decisions` row built from corrupted
    # (duplicate-venue) evidence, and no EM-6-only test would catch it.
    import src.foundation.ems.application.route_order as target

    def _rank_without_duplicate_check(
        candidates: list[VenueCandidate],
        weights: VenueScoreWeights = DEFAULT_VENUE_SCORE_WEIGHTS,
    ) -> list[RouteDecision]:
        if not candidates:
            raise ValueError("rank_venues: candidates must not be empty.")
        ranked = sorted(candidates, key=lambda c: (c.fee_bps, c.venue))
        return [
            RouteDecision(venue=c.venue, reason_codes=["STUB"], expected_cost_bps=c.fee_bps)
            for c in ranked
        ]

    monkeypatch.setattr(target, "rank_venues", _rank_without_duplicate_check)

    record = await route_order(
        repo, order_id=order_id, candidates=candidates, decided_at=datetime.now(timezone.utc)
    )

    # without the guard, a decision built from duplicate-venue candidates is
    # persisted -- proving the real (unpatched) check is what protects
    # `route_decisions`'s evidence integrity, not anything in this module.
    assert repo.insert_calls == 1
    assert record.decision.venue == "DUP"


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_route_order_perf_budget_many_candidates() -> None:
    """`route_order` builds two snapshots (`candidates_snapshot`,
    `score_snapshot`) per candidate on top of EM-5's O(n log n) rank -- this
    pins an upper bound so an accidental O(n^2) (e.g. re-scanning candidates
    inside the snapshot loop, or re-fetching from the repository per
    candidate) fails this test instead of only showing up as production
    latency."""
    candidates = _candidates(100)
    repo = FakeRouteDecisionRepository()

    start = time.perf_counter()
    for _ in range(200):
        repo.records.clear()
        await route_order(
            repo, order_id=uuid4(), candidates=candidates, decided_at=datetime.now(timezone.utc)
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 800, (
        f"route_order() too slow: {elapsed_ms:.1f}ms/200 calls x 100 candidates"
    )
