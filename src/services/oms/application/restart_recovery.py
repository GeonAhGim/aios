"""L4-18a/b (task-2151/task-2310) -- restart recovery: reclaim expired execution
leases + outbox SENDING re-entry/UNKNOWN transition + deny submit_order before
recovery completes.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6, §9 L4-18.
F6 defines 5 steps (① outbox lease expiry -> UNKNOWN ② delegate all UNKNOWN to
the resolver ③ RESYNC non-terminal orders ④ existing recovery_wiring
⑤ deny submit_order until complete). task-2151 (batches 1/2) covered
reclaiming execution_ownership `execution_leases` and ⑤ denying submit.
This batch (task-2310, L4-18b) picks up ①② --
`recover_stuck_outbox_commands` re-enters (PENDING) or transitions to UNKNOWN
outbox SENDING (lease-expired) rows per the §4.2/§4.4 state machine, and
orders that become UNKNOWN are handed straight off to
`unknown_resolver.resolve_unknown` (task-1604) -- do not reimplement.
③ (RESYNC of non-terminal orders) is still out of scope (decision).

This module provides pure building blocks only: the actual orchestration
(lease reclaim -> outbox recovery -> `recover_orders_on_startup` -> mark
complete) is carried out by the existing assembly point,
`execution_loop/recovery_wiring.py` (§C forbids duplicate context -- do not
build another orchestrator here).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.data.models.trading import OrderStatus
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.oms.application.outbox_commands import AdapterResolver
from src.services.oms.application.outbox_writes import OutboxWrites, utcnow
from src.services.oms.application.unknown_resolver import resolve_unknown
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort, OutboxRow
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)

RECOVERY_IN_PROGRESS_REASON = "OMS_RECOVERY_IN_PROGRESS"
_LEASE_RECLAIM_ACTOR = "main_process"
_LEASE_RECLAIM_ACTION_TYPE = "system.restart_recovery.leases"

DEFAULT_STUCK_OUTBOX_LEASE_SEC = 30
_STUCK_OUTBOX_RECOVERY_WORKER = "restart_recovery"
_REENTRY_REASON = "RESTART_RECOVERY_REENTRY"
_LEASE_EXPIRED_UNKNOWN_REASON = "RESTART_RECOVERY_LEASE_EXPIRED"


class RecoveryState:
    """Mutable state shared process-wide for whether restart recovery has completed.

    A plain bool flag, not an `asyncio.Event` -- `make_recovery_gate` must not
    wait for recovery to finish (`await event.wait()`) before letting a call
    through; it must deny immediately on the spot if recovery hasn't finished
    yet (§6 F6 ⑤). Exactly one instance is created per process lifetime,
    filled in by `recovery_wiring.run_startup_recovery`, and passed straight
    through to the gate constructor by `background_loops.py`."""

    def __init__(self) -> None:
        self._complete = False

    @property
    def complete(self) -> bool:
        return self._complete

    def mark_complete(self) -> None:
        self._complete = True


async def reclaim_expired_leases(pool: asyncpg.Pool) -> int:
    """Delete and reclaim `execution_leases` rows that have expired (`expires_at < now()`).

    A freshly restarted process doesn't know the dead process's `owner_id`
    (the `{host}:{pid}:{uuid}` form, `background_loops.py`), so it can't call
    `release_all(old_owner)` -- leases whose TTL has already passed would
    naturally be taken over by the next `acquire_or_renew_many` anyway (via
    the `WHERE ... expires_at < now()` clause in `postgres_repository.py`),
    but explicitly cleaning up right after restart leaves ownership state
    clean before the execution loop comes up. Rows whose TTL hasn't expired
    yet are left untouched -- another live process may legitimately still
    hold them."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM execution_leases WHERE expires_at < now()")
    reclaimed = int(result.split()[-1])
    if reclaimed:
        async with pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=_LEASE_RECLAIM_ACTOR,
                action_type=_LEASE_RECLAIM_ACTION_TYPE,
                decision_data={"reclaimed_leases": reclaimed},
                target_type="system",
                target_id=_LEASE_RECLAIM_ACTOR,
            )
    return reclaimed


def make_recovery_gate(state: RecoveryState, delegate: PreSubmitGate) -> PreSubmitGate:
    """§6 F6 ⑤ -- before recovery completes, never call `delegate` (usually
    `make_foundation_pre_submit_gate`) at all and unconditionally DENY.
    After recovery completes, each call simply re-checks `state.complete`
    (no separate caching) and delegates to `delegate`."""

    async def gate(context: OrderContext) -> GateDecision:
        if not state.complete:
            logger.info(
                "pre_submit_gate: 재시작 복구 미완료 — execution_id=%s 거부(%s)",
                context.execution_id,
                RECOVERY_IN_PROGRESS_REASON,
            )
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=(RECOVERY_IN_PROGRESS_REASON,),
            )
        return await delegate(context)

    return gate


async def recover_stuck_outbox_commands(
    pool: asyncpg.Pool,
    *,
    order_repo: OrderRepoPort,
    outbox_repo: OutboxRepoPort,
    resolve_adapter: AdapterResolver,
    risk_gate_repo: RiskGateRepository,
    worker_id: str = _STUCK_OUTBOX_RECOVERY_WORKER,
    lease_sec: int = DEFAULT_STUCK_OUTBOX_LEASE_SEC,
    limit: int = 500,
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """§6 F6 ①② -- transitions outbox SENDING rows whose lease has expired
    to re-entry (PENDING) or UNKNOWN per the §4.2/§4.4 state machine.

    CANCEL/MODIFY belong to the idempotent family (see the `outbox_commands.py`
    module docstring), so re-entering them unconditionally is safe. SUBMIT is
    only sent to UNKNOWN, per the no-resend principle (§5.4), when whether the
    adapter was actually called is uncertain (i.e. the `SENT` tx in
    `outbox_dispatcher.py` committed and the order is already SUBMITTED, but
    the subsequent finalize tx never landed) -- if the order is still
    VALIDATED (meaning even that tx never committed), zero adapter calls is
    guaranteed, so re-entry is safe. Orders that become UNKNOWN are handed
    straight off to `unknown_resolver.resolve_unknown` (do not reimplement,
    task-1604).
    """
    if risk_gate_repo is None:  # I-01 -- the safety-gate argument has no None default
        raise TypeError(
            "risk_gate_repo는 필수입니다(I-01) — None을 명시적으로 넘길 수 없습니다."
        )
    async with pool.acquire() as conn, conn.transaction():
        rows = await outbox_repo.reclaim_stuck_sending(
            conn, worker_id=worker_id, limit=limit, lease_sec=lease_sec
        )
    writes = OutboxWrites(
        outbox_repo=outbox_repo, order_repo=order_repo, worker_id=worker_id, clock=clock
    )
    newly_unknown: list[OrderView] = []
    for row in rows:
        if row.command_type != "SUBMIT":
            async with pool.acquire() as conn, conn.transaction():
                await outbox_repo.mark_retry(
                    conn, row.id, attempt=row.attempt, not_before=clock(),
                    last_error=_REENTRY_REASON, expected_worker=worker_id,
                )
            continue
        order = await _recover_stuck_submit(pool, order_repo, outbox_repo, writes, row, clock)
        if order is not None:
            newly_unknown.append(order)
    for order in newly_unknown:
        adapter = await resolve_adapter(order.tenant_id, order.exchange)
        await resolve_unknown(
            order.order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            order_repo=order_repo,
            clock=clock,
        )
    return len(rows)


async def _recover_stuck_submit(
    pool: asyncpg.Pool,
    order_repo: OrderRepoPort,
    outbox_repo: OutboxRepoPort,
    writes: OutboxWrites,
    row: OutboxRow,
    clock: Callable[[], datetime],
) -> OrderView | None:
    async with pool.acquire() as conn, conn.transaction():
        order = await order_repo.get_for_update(conn, row.order_id)
        if order.status not in (OrderStatus.VALIDATED, OrderStatus.SUBMITTED):
            # already finalized elsewhere (e.g. inbox) -- command exhausted
            await writes.done(conn, row)
            return None
        if order.status is OrderStatus.VALIDATED:
            await outbox_repo.mark_retry(
                conn, row.id, attempt=row.attempt, not_before=clock(),
                last_error=_REENTRY_REASON, expected_worker=writes.worker_id,
            )
            return None
        await writes.done(conn, row)  # fence first (§5.1) -- no resend
        return await writes.transition(
            conn, row, order, OrderStatus.UNKNOWN, OrderEvent.RESPONSE_LOST,
            _LEASE_EXPIRED_UNKNOWN_REASON, {"unknown_since": clock()},
        )
