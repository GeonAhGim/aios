"""EM-6 -- application/route_order.py: assemble a routing decision and
persist its evidence.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table
(`application/route_order.py` + `route_decisions` storage + integration),
§9 EM-6 (task-2120 decision, PM 2026-09-08).

This leaf does not compute fee tiers, liquidity depth, or venue scores --
that stays EM-4 (`domain/route/fee_model.py`/`liquidity_model.py`) and EM-5
(`domain/route/venue_scoring.py`)'s job. `route_order` only assembles the
already-scored candidates via `venue_scoring.rank_venues`, picks the winner,
and persists a reproducible record (`RouteDecisionRepository.insert_or_get`)
-- assembly, persistence, and evidence-recording, nothing else. It does not
submit any order to a venue (decision item 3): order transmission is wired
in EM-15+.

Single-candidate fallback (DoD 2) and empty-candidate rejection are already
`rank_venues`'s contract (`ONLY_VENUE` reason code, `ValueError` on empty)
-- this module turns the latter into the EM_NO_ROUTE domain error
(`NoRouteAvailableError`) instead of leaking a bare `ValueError`, and never
calls the repository when there is nothing to route (fail-closed: no
`route_decisions` row is written for a rejected routing attempt).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.domain.route.venue_scoring import (
    DEFAULT_VENUE_SCORE_WEIGHTS,
    VenueCandidate,
    VenueScoreWeights,
    rank_venues,
)
from src.foundation.ems.ports.route_decision_repository import (
    RouteDecisionRecord,
    RouteDecisionRepository,
)


class NoRouteAvailableError(LookupError):
    """EM_NO_ROUTE -- no candidate venues were supplied for `order_id`.

    Fail-closed (§3 error taxonomy): raised before the repository is ever
    called, so a rejected routing attempt never produces a
    `route_decisions` row.
    """

    def __init__(self, order_id: UUID) -> None:
        super().__init__(
            f"order_id={order_id}: no candidate venues available for routing (EM_NO_ROUTE)."
        )
        self.order_id = order_id


def _candidate_snapshot(candidate: VenueCandidate) -> dict[str, Any]:
    return {
        "venue": candidate.venue,
        "fee_bps": str(candidate.fee_bps),
        "liquidity_score": str(candidate.liquidity_score),
    }


def _decision_snapshot(decision: RouteDecision) -> dict[str, Any]:
    return {
        "venue": decision.venue,
        "reason_codes": list(decision.reason_codes),
        "expected_cost_bps": str(decision.expected_cost_bps),
    }


async def route_order(
    repo: RouteDecisionRepository,
    *,
    order_id: UUID,
    candidates: Sequence[VenueCandidate],
    decided_at: datetime,
    weights: VenueScoreWeights = DEFAULT_VENUE_SCORE_WEIGHTS,
) -> RouteDecisionRecord:
    """Rank `candidates` (EM-5), persist the winner keyed by `order_id`, and
    return the stored record.

    Idempotent: a second call with the same `order_id` returns the
    previously stored record rather than creating a second row (DoD 3 --
    enforced by the repository's `insert_or_get`, not recomputed here).
    """
    if not candidates:
        raise NoRouteAvailableError(order_id)

    ranked = rank_venues(candidates, weights)
    winner = ranked[0]

    return await repo.insert_or_get(
        order_id=order_id,
        decision=winner,
        candidates_snapshot=[_candidate_snapshot(candidate) for candidate in candidates],
        score_snapshot=[_decision_snapshot(decision) for decision in ranked],
        weights_snapshot={
            "fee_weight": str(weights.fee_weight),
            "liquidity_weight": str(weights.liquidity_weight),
        },
        decided_at=decided_at,
    )
