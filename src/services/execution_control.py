"""FD-16.3/16.5 — Execution start/pause/max drawdown/retire (start/pause/set_max_drawdown/retire).

Spec: functional_specification_v1.20.md#FD-16.3, 16.5, item 06 §6.1, item 02 §2.2. To
comply with P6 (300-line file cap), only the 4 "control" actions were moved
from execution_service.py into this module (create_execution/convert_to_live
remain as "creation" actions). Each method on `ExecutionService` is a thin
delegation that passes `self._pool`, etc. to these functions; the externally
visible class contract (`ExecutionService.start()`, etc.) is unchanged.

8.6-B Kill Switch priority rule — An execution already switched to PAUSED
(paused_by=SAFETY_LAYER) by Watchdog/Circuit Breaker (FD-9) is rejected even
if the user presses "start" (system triggers take priority over user actions).
Only executions paused by the user themselves (paused_by=USER) can be resumed
by that user.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.services.execution_types import ExecutionControlError, ExecutionSummary
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate


async def start(
    pool: asyncpg.Pool,
    pre_start_gate: PreSubmitGate,
    execution_id: int,
    user_id: UUID,
) -> ExecutionSummary:
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT user_id, status, mode, paused_by, allocated_capital, exchange "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        if execution is None:
            raise ExecutionControlError("Execution not found.")
        if execution["user_id"] != user_id:
            raise ExecutionControlError("You can only control your own executions.")
        if execution["status"] == "RETIRED":
            raise ExecutionControlError("A stopped execution cannot be restarted.")

        if execution["status"] == "PAUSED" and execution["paused_by"] == "SAFETY_LAYER":
            raise ExecutionControlError(
                "Execution stopped by safety mechanism (Watchdog/Circuit Breaker) — "
                "users cannot restart it directly."
            )

        if execution["status"] == "PENDING_APPROVAL" and execution["mode"] == "LIVE":
            approved = await conn.fetchval(
                "SELECT status FROM approval_requests "
                "WHERE trigger_source = 'execution_high_allocation' "
                "AND (context->>'execution_id')::bigint = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                execution_id,
            )
            if approved != "APPROVED":
                raise ExecutionControlError(
                    f"LIVE execution must be approved before starting (current approval: "
                    f"{approved or 'no request'})."
                )

        # Full-audit §6 — DEPLOYMENT gate (item 48 §3 gate 1). Inspect *before*
        # the actual transition to RUNNING; if rejected, skip the UPDATE and leave
        # the state unchanged (same FSM-invariant principle as order_service).
        # EO-05 — gate is now a required argument, so it is always evaluated.
        decision = await pre_start_gate(
            OrderContext(
                user_id=user_id,
                execution_id=execution_id,
                exchange=execution["exchange"],
                mandate_revision_id=None,  # Column does not exist — awaiting migration
            )
        )
        if decision.outcome != GateOutcome.ALLOW:
            raise ExecutionControlError(
                f"Risk gate rejected start: {decision.reason_codes}"
            )

        row = await conn.fetchrow(
            "UPDATE strategy_executions "
            "SET status = 'RUNNING', paused_by = NULL, "
            "started_at = COALESCE(started_at, now()) "
            "WHERE id = $1 AND status = $2 "
            "RETURNING status, mode, exchange, allocated_capital",
            execution_id,
            execution["status"],
        )
        if row is None:
            # Red-team audit (docs/RED_TEAM_FINDINGS.md #08) — by asserting on the
            # status we just read, if the Watchdog (separate process) committed a
            # safety pause first, this UPDATE itself will match zero rows.
            # 8.6-B Kill Switch priority — user requests must not silently overwrite
            # a safety pause.
            raise ExecutionControlError(
                "Another process changed this execution's status just now — "
                "re-query and retry (a safety mechanism may have paused it)."
            )
    return ExecutionSummary(
        id=execution_id,
        status=row["status"],
        mode=row["mode"],
        exchange=row["exchange"],
        allocated_capital=row["allocated_capital"],
    )


async def pause(
    pool: asyncpg.Pool,
    execution_id: int,
    *,
    paused_by: str = "USER",
    user_id: UUID | None = None,
) -> ExecutionSummary:
    if paused_by not in ("USER", "SAFETY_LAYER"):
        raise ExecutionControlError(f"Unknown paused_by value: {paused_by}")

    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT user_id, status, mode, exchange, allocated_capital "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        if execution is None:
            raise ExecutionControlError("Execution not found.")
        if paused_by == "USER":
            if user_id is None or execution["user_id"] != user_id:
                raise ExecutionControlError("You can only control your own executions.")
        if execution["status"] != "RUNNING":
            raise ExecutionControlError(
                f"Can only pause RUNNING executions (current: {execution['status']})."
            )

        row = await conn.fetchrow(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = $2 "
            "WHERE id = $1 AND status = 'RUNNING' "
            "RETURNING status, mode, exchange, allocated_capital",
            execution_id,
            paused_by,
        )
        if row is None:
            # #08 — even if it was RUNNING when just read, another path
            # (Watchdog or concurrent request) may have changed the state.
            # Report a conflict instead of silently overwriting.
            raise ExecutionControlError(
                "Another process already paused this execution — re-query and try again."
            )
    return ExecutionSummary(
        id=execution_id,
        status=row["status"],
        mode=row["mode"],
        exchange=row["exchange"],
        allocated_capital=row["allocated_capital"],
    )


async def set_max_drawdown(
    pool: asyncpg.Pool,
    execution_id: int,
    user_id: UUID,
    max_drawdown_pct: Decimal | None,
) -> ExecutionSummary:
    """ZuluTrade-style "risk management" (ZuluGuard) — sets a per-execution
    loss limit (%). risk_guard_service.py::evaluate_all_running() periodically
    compares realized + unrealized P&L against this limit and auto-pauses the
    execution (paused_by='SAFETY_LAYER') on exceedance. Setting to None
    disables the guard (default)."""
    if max_drawdown_pct is not None and not (0 < max_drawdown_pct <= 100):
        raise ExecutionControlError("Loss limit must be greater than 0 and at most 100.")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE strategy_executions SET max_drawdown_pct = $3 "
            "WHERE id = $1 AND user_id = $2 "
            "RETURNING status, mode, exchange, allocated_capital, max_drawdown_pct",
            execution_id,
            user_id,
            max_drawdown_pct,
        )
    if row is None:
        raise ExecutionControlError("You can only control your own executions.")
    return ExecutionSummary(
        id=execution_id,
        status=row["status"],
        mode=row["mode"],
        exchange=row["exchange"],
        allocated_capital=row["allocated_capital"],
        max_drawdown_pct=row["max_drawdown_pct"],
    )


async def retire(
    pool: asyncpg.Pool,
    execution_id: int,
    user_id: UUID,
    *,
    liquidation: str = "KEEP_POSITIONS",
) -> ExecutionSummary:
    if liquidation not in ("IMMEDIATE_MARKET", "KEEP_POSITIONS"):
        raise ExecutionControlError(f"Unknown liquidation method: {liquidation}")

    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT user_id, status, mode, exchange, allocated_capital "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        if execution is None:
            raise ExecutionControlError("Execution not found.")
        if execution["user_id"] != user_id:
            raise ExecutionControlError("You can only control your own executions.")
        if execution["status"] not in ("RUNNING", "PAUSED"):
            raise ExecutionControlError(
                f"Can only stop RUNNING/PAUSED executions (current: {execution['status']})."
            )

        row = await conn.fetchrow(
            "UPDATE strategy_executions "
            "SET status = 'RETIRED', retire_liquidation = $2, retired_at = now() "
            "WHERE id = $1 AND status IN ('RUNNING', 'PAUSED') "
            "RETURNING status, mode, exchange, allocated_capital",
            execution_id,
            liquidation,
        )
        if row is None:
            # Same family as #08 — if already transitioned to RETIRED/another
            # state concurrently, do not silently fake success.
            raise ExecutionControlError(
                "Another process already changed this execution's status — re-query."
            )
    return ExecutionSummary(
        id=execution_id,
        status=row["status"],
        mode=row["mode"],
        exchange=row["exchange"],
        allocated_capital=row["allocated_capital"],
    )
