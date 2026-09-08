"""asyncpg implementation of RiskSignalRepository -- `risk_signal`.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 table row 105, §6 row 453
(dedupe rule), §9 R-46 (task-2136). `insert_if_new` is the only write
path: `INSERT ... ON CONFLICT (dedupe_key) DO NOTHING RETURNING id` --
a row means new, none means it was already recorded in the same
5-minute window (RSK-008), not an error.

`dedupe_key_for` is exported (not private) because `application/
intraday_monitor.py` must compute the same key before calling
`insert_if_new` -- if `floor(as_of, 5min)` were implemented separately
per call site, the DoD(b) boundary assertions (04:59:59 vs 05:00:00 vs
05:04:59) could drift between caller and repository. Keeping it in one
place rules that out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.foundation.risk_gate.domain.models import (
    RiskSignal,
    RiskSignalSeverity,
    RiskSignalState,
    RiskSignalType,
)


def dedupe_key_for(signal_type: str, scope_ref: str, as_of: datetime) -> str:
    """§6 row 453: `f"{type}:{scope_ref}:{floor(as_of, 5min)}"`.

    Converts to UTC before flooring so the same instant always lands in
    the same 5-minute bucket regardless of the caller's input tz."""
    utc = as_of.astimezone(timezone.utc)
    floored = utc.replace(minute=(utc.minute // 5) * 5, second=0, microsecond=0)
    return f"{signal_type}:{scope_ref}:{floored.isoformat()}"


def _row_to_signal(row: asyncpg.Record) -> RiskSignal:
    return RiskSignal(
        id=row["id"],
        tenant_id=row["tenant_id"],
        type=RiskSignalType(row["type"]),
        severity=RiskSignalSeverity(row["severity"]),
        dedupe_key=row["dedupe_key"],
        as_of=row["as_of"],
        source=row["source"],
        evidence_ref=row["evidence_ref"],
        state=RiskSignalState(row["state"]),
        safety_control_id=row["safety_control_id"],
    )


class PostgresSignalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_if_new(
        self,
        *,
        signal_id: UUID,
        dedupe_key: str,
        tenant_id: UUID,
        signal_type: str,
        severity: str,
        as_of: datetime,
        source: str,
        evidence_ref: str | None = None,
        safety_control_id: UUID | None = None,
    ) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO risk_signal "
                "(id, tenant_id, type, severity, dedupe_key, as_of, source, "
                " evidence_ref, safety_control_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                "ON CONFLICT (dedupe_key) DO NOTHING "
                "RETURNING id",
                signal_id,
                tenant_id,
                signal_type,
                severity,
                dedupe_key,
                as_of,
                source,
                evidence_ref,
                safety_control_id,
            )
        return row is not None

    async def list_open(self, tenant_id: UUID) -> tuple[RiskSignal, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM risk_signal WHERE tenant_id = $1 AND state = 'OPEN'",
                tenant_id,
            )
        return tuple(_row_to_signal(row) for row in rows)


__all__ = ["PostgresSignalRepository", "dedupe_key_for"]
