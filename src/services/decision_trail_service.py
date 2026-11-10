"""Read execution audit and risk decisions for the execution owner."""

from __future__ import annotations

from typing import overload
from uuid import UUID

import asyncpg

from src.services.decision_trail import RiskTrailEntry, TrailEntry, build_decision_trail
from src.services.execution_types import ExecutionControlError

_AUDIT_LOG_SQL = """
SELECT action_type, actor_agent, created_at, decision_data
FROM audit_log
WHERE target_type = 'strategy_execution' AND target_id = $1
  AND action_type IN ('execution.converted_to_live', 'execution.exposure_expanded')
ORDER BY log_id
"""

_RISK_DECISION_SQL = """
SELECT decision_id, gate_kind, outcome, reason_codes, rule_version, evaluated_at
FROM risk_decision
WHERE tenant_id = $2 AND execution_ref = $1
ORDER BY evaluated_at DESC
LIMIT 200
"""

# Legacy response contract (RiskTrailEntry list), kept for callers without an owner.
_LEGACY_TRAIL_SQL = """
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
    """Compose the recorded trail after checking execution ownership."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @overload
    async def get_trail(self, execution_id: int) -> list[RiskTrailEntry]: ...

    @overload
    async def get_trail(self, execution_id: int, user_id: UUID) -> list[TrailEntry]: ...

    async def get_trail(
        self, execution_id: int, user_id: UUID | None = None
    ) -> list[RiskTrailEntry] | list[TrailEntry]:
        """Without ``user_id``: legacy risk-only entries; with it: owner-checked merged trail."""
        if user_id is None:
            return await self._legacy_trail(execution_id)
        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id, mode FROM strategy_executions WHERE id = $1", execution_id
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if execution["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 조회할 수 있습니다.")
            audit_rows = await conn.fetch(_AUDIT_LOG_SQL, str(execution_id))
            risk_rows = await conn.fetch(_RISK_DECISION_SQL, str(execution_id), user_id)
        return build_decision_trail(
            [dict(row) for row in audit_rows], [dict(row) for row in risk_rows]
        )

    async def _legacy_trail(self, execution_id: int) -> list[RiskTrailEntry]:
        rows = await self._pool.fetch(_LEGACY_TRAIL_SQL, execution_id)
        return [
            RiskTrailEntry(
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
            for row in rows
        ]
