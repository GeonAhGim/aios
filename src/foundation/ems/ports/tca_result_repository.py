"""EM-14 -- `tca_results` repository port.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`application/compute_tca.py` + storage + integration), #5 (`(parent_id,
revision)` is the unique/idempotency key), #4 EM-A5 (a post-hoc data
correction is recomputed as a new revision, not an in-place update --
`tca_results` is append-only, same WORM posture as `route_decisions`).

Follows the `src/foundation` convention (standard 71 §4): `application/
compute_tca.py` depends only on this `Protocol`, never on a concrete
asyncpg adapter. The Postgres adapter and the `tca_results` migration are
a separate leaf (task-4013) -- this port is the contract that adapter
implements against, same split as EM-6's `route_decisions` port existing
before its concrete adapter was wired in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.foundation.ems.contracts.v1 import TcaResult


class TcaResultNotFoundError(LookupError):
    """`EM_TCA_RESULT_NOT_FOUND` -- no `tca_results` row for the requested
    `parent_id` (and, for revision lookups, `revision`)."""


@dataclass(frozen=True)
class TcaResultRecord:
    """One persisted `tca_results` row.

    `revision` starts at `1` for the first computation of a given
    `parent_id` and increments on every recompute triggered by a
    post-hoc fill/quote correction (EM-A5) -- prior revisions are never
    overwritten, so the recompute history itself is the audit trail.
    """

    tca_id: UUID
    parent_id: UUID
    revision: int
    result: TcaResult
    computed_at: datetime
    created_at: datetime


class TcaResultRepository(Protocol):
    """`tca_results` is append-only (WORM, no UPDATE/DELETE) -- there is no
    update method, only insert-or-return-existing and lookup, mirroring
    `RouteDecisionRepository` (`ports/route_decision_repository.py`)."""

    async def insert_or_get(
        self,
        *,
        parent_id: UUID,
        revision: int,
        result: TcaResult,
        computed_at: datetime,
    ) -> TcaResultRecord:
        """Insert one row for `(parent_id, revision)`, or -- if a row for
        that key already exists -- return the existing row unchanged
        (idempotent: a repeated compute for the same revision must not
        create a second row, and must not raise on the losing side of a
        race)."""
        ...

    async def get_by_revision(self, parent_id: UUID, revision: int) -> TcaResultRecord | None: ...

    async def get_latest(self, parent_id: UUID) -> TcaResultRecord | None:
        """Return the highest-`revision` row for `parent_id`, or `None` if
        no TCA has ever been computed for it."""
        ...
