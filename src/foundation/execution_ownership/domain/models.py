"""EO-01 — Execution lease value object.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.1. I/O·asyncpg imports prohibited (SCAFFOLD zone purity) — this module
only represents a single row of the `execution_leases` table; persistence is
the responsibility of ports/adapters (EO-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def require_aware_utc(value: datetime) -> datetime:
    """Only lets tz-aware datetimes through. Checking `tzinfo is None` alone
    would let a malformed tzinfo (e.g. an adversarially crafted custom
    tzinfo) whose `utcoffset()` returns None slip past and leak an
    uncontrolled TypeError at comparison time -- this must fail-closed with
    ValueError instead."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime is not allowed — use tz-aware UTC only")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """Single row of `execution_leases` table (§3.1). `fencing_token` increases
    only when ownership changes — this value object itself does not enforce that
    rule; `rules.is_lease_available`/repository SQL (§5.1) enforces it."""

    execution_id: int
    owner_id: str
    fencing_token: int
    heartbeat_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.heartbeat_at)
        require_aware_utc(self.expires_at)
