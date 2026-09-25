"""EO-01 — Pure decision rules for execution ownership.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.2, §4.1(I-02). No I/O or asyncpg imports (SCAFFOLD zone purity) —
actual acquisition/renewal is the responsibility of the EO-02 repository adapter
(§5.1 conditional UPSERT); this function only decides "may this lease be requested?".
"""
from __future__ import annotations

from datetime import datetime

from src.foundation.execution_ownership.domain.models import ExecutionLease, require_aware_utc


def is_lease_available(
    existing: ExecutionLease | None,
    *,
    now: datetime,
    requesting_owner: str,
) -> bool:
    """Return True if no lease exists, it has expired, or the requester already holds it.
    Return False if another owner holds a non-expired lease (§4.1 "tick only under a
    single process with a valid lease"). Expiry boundary is strict — same as §5.1 SQL
    `expires_at < now()` — `expires_at == now` is still considered valid.

    Full-audit 2026-09-06 P1-C(task-1722) — zero src importers (not called from runtime
    code) is intentional: the §5.1 conditional UPSERT (`postgres_repository.py`
    `acquire_or_renew_many`) atomically re-implements this decision as a SQL `WHERE`
    clause (eliminating check-then-update races requires a single round-trip SQL call,
    which cannot invoke this Python function). This function is the executable spec of
    the decision rules that SQL must match exactly, and the boundary-value tests in
    `tests/foundation/unit/execution_ownership/test_rules.py` prove that match —
    it is not an entry point that calls the adapter."""
    require_aware_utc(now)
    if existing is None:
        return True
    if existing.owner_id == requesting_owner:
        return True
    return existing.expires_at < now
