"""EM-6 `application/route_order.py` + `route_decisions` integration tests
-- real DB (TEST_DATABASE_URL).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md#EM-6 (task-2120
decision, PM 2026-09-08).

DoD covered here: (1) evidence is reproducible -- stored
candidates/weights recompute (via EM-5's `rank_venues`, not reimplemented)
to the same stored decision; (2) single-candidate fallback is unconditional
and carries `ONLY_VENUE`; empty candidates is a fail-closed domain
rejection with no row written; (3) idempotency -- concurrent calls for the
same `order_id` leave exactly one row.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ems.adapters.postgres_route_decision_repository import (
    PostgresRouteDecisionRepository,
)
from src.foundation.ems.application.route_order import NoRouteAvailableError, route_order
from src.foundation.ems.domain.route.venue_scoring import (
    VenueCandidate,
    VenueScoreWeights,
    rank_venues,
)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresRouteDecisionRepository(pool)


async def _row_count(pool: asyncpg.Pool, order_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM route_decisions WHERE order_id = $1", order_id
        )


async def test_route_order_persists_reproducible_decision(pool, repo):
    """DoD 1 -- stored candidates/weights recompute (EM-5) to the exact
    same decision, not just a summary that cannot be reproduced."""
    order_id = uuid4()
    candidates = [
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("2.5"), liquidity_score=Decimal("0.6")),
        VenueCandidate(venue="BRAVO", fee_bps=Decimal("1.0"), liquidity_score=Decimal("0.9")),
        VenueCandidate(venue="CHARLIE", fee_bps=Decimal("0.5"), liquidity_score=Decimal("0.2")),
    ]
    weights = VenueScoreWeights(fee_weight=Decimal("2"), liquidity_weight=Decimal("50"))
    decided_at = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    record = await route_order(
        repo, order_id=order_id, candidates=candidates, decided_at=decided_at, weights=weights
    )

    assert record.order_id == order_id
    assert await _row_count(pool, order_id) == 1

    # Reproduce from the persisted snapshot alone -- via EM-5's rank_venues,
    # not any scoring logic re-implemented in this test or in route_order.
    rebuilt_candidates = [
        VenueCandidate(
            venue=item["venue"],
            fee_bps=Decimal(item["fee_bps"]),
            liquidity_score=Decimal(item["liquidity_score"]),
        )
        for item in record.candidates_snapshot
    ]
    rebuilt_weights = VenueScoreWeights(
        fee_weight=Decimal(record.weights_snapshot["fee_weight"]),
        liquidity_weight=Decimal(record.weights_snapshot["liquidity_weight"]),
    )
    recomputed = rank_venues(rebuilt_candidates, rebuilt_weights)

    assert recomputed[0].venue == record.decision.venue
    assert recomputed[0].reason_codes == record.decision.reason_codes
    assert recomputed[0].expected_cost_bps == record.decision.expected_cost_bps
    assert [d.venue for d in recomputed] == [item["venue"] for item in record.score_snapshot]


async def test_route_order_single_candidate_fallback(pool, repo):
    """DoD 2 -- one candidate is always selected regardless of its score,
    and the fact is recorded via the ONLY_VENUE reason code."""
    order_id = uuid4()
    candidates = [
        VenueCandidate(venue="LONE", fee_bps=Decimal("999"), liquidity_score=Decimal("0")),
    ]

    record = await route_order(
        repo,
        order_id=order_id,
        candidates=candidates,
        decided_at=datetime.now(timezone.utc),
    )

    assert record.decision.venue == "LONE"
    assert record.decision.reason_codes == ["ONLY_VENUE"]


async def test_route_order_rejects_empty_candidates(pool, repo):
    """DoD 2 negative -- zero candidates is a fail-closed domain rejection,
    no route_decisions row is written."""
    order_id = uuid4()

    with pytest.raises(NoRouteAvailableError):
        await route_order(
            repo, order_id=order_id, candidates=[], decided_at=datetime.now(timezone.utc)
        )

    assert await _row_count(pool, order_id) == 0
    assert await repo.get_by_order_id(order_id) is None


async def test_route_order_concurrent_calls_are_idempotent(pool, repo):
    """DoD 3 -- 20 concurrent route_order calls for the same order_id leave
    exactly one route_decisions row."""
    order_id = uuid4()
    candidates = [
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("2.5"), liquidity_score=Decimal("0.6")),
        VenueCandidate(venue="BRAVO", fee_bps=Decimal("1.0"), liquidity_score=Decimal("0.9")),
    ]
    decided_at = datetime.now(timezone.utc)

    results = await asyncio.gather(
        *(
            route_order(
                repo, order_id=order_id, candidates=candidates, decided_at=decided_at
            )
            for _ in range(20)
        )
    )

    assert await _row_count(pool, order_id) == 1
    assert len({record.decision_id for record in results}) == 1
