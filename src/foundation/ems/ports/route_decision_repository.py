"""EM-6 -- `route_decisions` repository port.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table
(`adapters/postgres_*.py` + migration for `route_decisions`), §9 EM-6.

Follows the `src/foundation` convention (standard 71 §4): domain/application
code depends only on this `Protocol`, never on the concrete asyncpg adapter
in `adapters/postgres_route_decision_repository.py`. `RouteDecisionRecord`
is the persisted shape -- it carries the winning `RouteDecision` (EM-1
contract) plus the raw inputs (`candidates_snapshot`, `weights_snapshot`)
and the full ranking (`score_snapshot`) needed to reproduce the decision
later (DoD 1), without this leaf recomputing or re-storing the scoring
logic itself (that stays EM-4/EM-5's job).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.foundation.ems.contracts.v1 import RouteDecision


@dataclass(frozen=True)
class RouteDecisionRecord:
    """One persisted `route_decisions` row.

    `candidates_snapshot` holds the raw `VenueCandidate` inputs
    (`venue`/`fee_bps`/`liquidity_score`, as decimal strings) and
    `score_snapshot` holds every candidate's resulting `RouteDecision`
    (EM-5's full ranked output, winner first) -- together with
    `weights_snapshot` they are sufficient to re-run
    `domain.route.venue_scoring.rank_venues` and get back the same
    `decision` (DoD 1's reproducibility requirement).
    """

    decision_id: UUID
    order_id: UUID
    decision: RouteDecision
    candidates_snapshot: list[dict[str, Any]]
    score_snapshot: list[dict[str, Any]]
    weights_snapshot: dict[str, Any]
    decided_at: datetime
    created_at: datetime


class RouteDecisionRepository(Protocol):
    """`route_decisions` is append-only (WORM, no UPDATE/DELETE) -- there is
    no update method, only insert-or-return-existing and lookup."""

    async def insert_or_get(
        self,
        *,
        order_id: UUID,
        decision: RouteDecision,
        candidates_snapshot: list[dict[str, Any]],
        score_snapshot: list[dict[str, Any]],
        weights_snapshot: dict[str, Any],
        decided_at: datetime,
    ) -> RouteDecisionRecord:
        """Insert one row for `order_id`, or -- if a row for `order_id`
        already exists -- return that existing row unchanged (DoD 3
        idempotency: concurrent calls for the same `order_id` must not
        create a second row, and must not raise on the losing side)."""
        ...

    async def get_by_order_id(self, order_id: UUID) -> RouteDecisionRecord | None: ...
