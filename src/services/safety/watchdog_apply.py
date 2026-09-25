"""Applying a watchdog HALT/LIQUIDATE decision — split out of `watchdog_process.py`
to stay under the P6 300-line cap (architecture_guard.py `SRC_LINE_CAP`).

Control creation is delegated entirely to `KillSwitchService.activate` (DoD(f)).
Only for LIQUIDATE, its result (control id/fence_token) is used to INSERT a
`liquidation_request` row as REQUESTED (§4 lines 426-430) — since activate()
commits its own transaction before returning (§5 "transaction boundary";
wrapping it while holding a connection would deadlock, P1), the two INSERTs are
separate transactions (a crash in between is a known residual risk). Unlike a
fan-out failure, a failure of this INSERT is core to the decision's outcome, so
it is not swallowed.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.core.safety.watchdog import WatchdogAction, WatchdogDecision
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService

logger = logging.getLogger(__name__)

# Must match the system actor row seeded by e5a8c5d4f6b7_liquidation_request.py
# — GLOBAL activate() requires a real users FK, but this decision is grounded
# in the system as a whole rather than any single tenant, so there is no
# tenant to borrow from (see the migration docstring).
WATCHDOG_SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


async def apply_decision(
    pool: asyncpg.Pool, decision: WatchdogDecision, kill_switch: KillSwitchService
) -> None:
    view = await kill_switch.activate(
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason=decision.reason,
        actor_subject_id=WATCHDOG_SYSTEM_ACTOR_ID,
        actor_is_admin=True,
        trace_id=uuid4(),
    )

    liquidation_request_id: UUID | None = None
    if decision.action == WatchdogAction.LIQUIDATE:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO liquidation_request "
                "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
                "VALUES ($1, 'GLOBAL', '', 'REQUESTED', 'watchdog_process', $2) "
                "RETURNING id",
                view.id,
                view.fence_token,
            )
        liquidation_request_id = row["id"]

    request_id_str = str(liquidation_request_id) if liquidation_request_id else None
    async with pool.acquire() as conn, conn.transaction():
        await record_audit_log(
            conn,
            actor_agent="watchdog_process",
            action_type="watchdog.decision.applied",
            decision_data={
                "action": decision.action.value,
                "reason": decision.reason,
                "control_id": str(view.id),
                "fence_token": view.fence_token,
                "liquidation_request_id": request_id_str,
            },
            target_type="system",
            target_id="all_running_executions",
        )
    logger.critical(
        "Watchdog %s 발동: %s (control=%s, fence=%s, liquidation_request=%s)",
        decision.action.value,
        decision.reason,
        view.id,
        view.fence_token,
        liquidation_request_id,
    )
