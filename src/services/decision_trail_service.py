"""LIVE promotion decision trail read service (J8 trail W2).

Reads the audit_log conversion/expansion records and the risk_decision rows of one
execution (read-only) and merges them with the pure `build_decision_trail` (W1).
The ownership check mirrors `LiveReadinessService`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from src.services.decision_trail import TrailEntry, build_decision_trail
from src.services.execution_types import ExecutionControlError

# Conversion and expansion records both key on the new LIVE execution id.
_AUDIT_SQL = """
SELECT action_type, actor_agent, created_at, decision_data
FROM audit_log
WHERE target_type = 'strategy_execution' AND target_id = $1
  AND action_type IN ('execution.converted_to_live', 'execution.exposure_expanded')
ORDER BY log_id
"""

_RISK_SQL = """
SELECT decision_id, gate_kind, outcome, reason_codes, rule_version, evaluated_at
FROM risk_decision
WHERE tenant_id = $2 AND execution_ref = $1
ORDER BY evaluated_at DESC
LIMIT 200
"""


class DecisionTrailService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_trail(self, execution_id: int, user_id: UUID) -> list[TrailEntry]:
        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id FROM strategy_executions WHERE id = $1", execution_id
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if execution["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 조회할 수 있습니다.")
            ref = str(execution_id)
            audit = await conn.fetch(_AUDIT_SQL, ref)
            risk = await conn.fetch(_RISK_SQL, ref, user_id)
        audit_rows: list[dict[str, Any]] = [dict(r) for r in audit]
        risk_rows: list[dict[str, Any]] = [dict(r) for r in risk]
        return build_decision_trail(audit_rows, risk_rows)
