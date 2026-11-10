"""Read execution audit and risk decisions for the execution owner."""

from __future__ import annotations

from uuid import UUID

import asyncpg

from src.services.decision_trail import TrailEntry, build_decision_trail
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


class DecisionTrailService:
    """Compose the recorded trail after checking execution ownership."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_trail(self, execution_id: int, user_id: UUID) -> list[TrailEntry]:
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
