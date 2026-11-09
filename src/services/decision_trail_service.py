"""Decision trail query service (J8 trail).

Reads ``risk_decision`` rows and returns ``TrailEntry`` instances.
"""

from __future__ import annotations

import asyncpg

from src.services.decision_trail import TrailEntry

_TRAIL_SQL = """
SELECT decision_id, gate_kind, outcome, reason_codes, rule_version,
       evaluated_at, expires_at, trace_id,
       COALESCE(
           string_agg(DISTINCT rr.rule_id || ': ' || rr.outcome, '; ' ORDER BY rr.rule_id),
           ''
       ) AS summary
FROM risk_decision rd
LEFT JOIN jsonb_to_recordset(rd.rule_results) AS rr(
    rule_id text, outcome text, reason_code text,
    observed numeric, limit numeric, unit text, missing_fields text[]
) ON true
WHERE rd.execution_id = $1
ORDER BY rd.evaluated_at
"""


class DecisionTrailService:
    """Fetches decision trail entries for a given execution."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_trail(self, execution_id: int) -> list[TrailEntry]:
        rows = await self._pool.fetch(_TRAIL_SQL, execution_id)
        result: list[TrailEntry] = []
        for row in rows:
            result.append(
                TrailEntry(
                    decision_id=row["decision_id"],
                    kind=str(row["gate_kind"]),
                    outcome=str(row["outcome"]),
                    reason_codes=tuple(row["reason_codes"]),
                    rule_version=row["rule_version"],
                    evaluated_at=row["evaluated_at"],
                    expires_at=row["expires_at"],
                    trace_id=row["trace_id"],
                    summary=row["summary"] or "",
                )
            )
        return result
