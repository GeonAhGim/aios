"""9.1 — Watchdog process skeleton (separate process, communicates only via the heartbeat file).

Spec: 기능설계문서_v1.20.md#FD-9.1/FD-9.2, 정책문서 8.6-A

Policy doc 8.6-A's "independent health-check process, fully isolated from main" principle —
launched via `python -m src.watchdog_process` as a separate OS process from main.py (uvicorn).
Shares no memory with the main process (the only shared surface is the file timestamp in
core/safety/heartbeat.py and Postgres — main.py's InProcessEventBus/app.state are simply
unreachable from this script, a different OS process).

Deviation (honest reduction, partially resolved by user approval 2026-09-02) — FD-9.1's loss_pct
calculation (compute_equity) needed the real order-fill pipeline; now that execution_loop is
actually running, the stub that always returned 0 has been removed. It now sums
(allocated_capital + realized_pnl) across RUNNING executions as a system-wide equity
approximation — computable from the DB alone without exchange credentials, so it doesn't violate
the "fully isolated from main process" principle.

Remaining approximation limits (honest reduction): (1) positions.unrealized_pnl has no
mark-to-market update path and is always 0 (only recorded as realized_pnl once a position
closes). (2) Multi-tenancy — RUNNING executions across all users are summed into one system-wide
number. exchange_healthy calls Bitget's public ticker API (no signature verification; confirmed
to succeed even with empty-string keys) — an account-independent infrastructure signal, so
multi-tenancy isn't an issue here.

9.3 Split-Brain diagnosis (core/safety/split_brain.py, wired in for the first time here) — each
cycle also checks the DB connection separately, to distinguish a "DB-only isolated failure". When
diagnosed as DB_ISOLATED_FAILURE, no forced action is taken (a forced action is only meaningful
under the assumption the DB itself is down, so the diagnosis is just logged).

Applying the HALT/LIQUIDATE decision (only when Split-Brain judges it is not a DB-only isolated
failure) — since R-51 (task-2357) this is delegated to `KillSwitchService.activate`
(scope=GLOBAL): the RUNNING-execution transition to paused_by='SAFETY_LAYER', the paper_control
fan-out, and the open_order_sweeper all happen inside that call (§4.3 line 412). The watchdog no
longer reimplements this transition itself (I3 — `INSERT INTO safety_control` must have exactly
one call site, `postgres_repository.py`). `exchange_adapters={}` is passed deliberately — since
credential_resolver is intentionally not wired in, open_order_sweeper's actual exchange cancel
calls all end up as `adapter_failed` (the local DB transition still happens; DoD(h) — this leaf
does not send orders).

The watchdog.decision.triggered notification is not published directly by this process
(InProcessEventBus cannot cross process boundaries) — the audit_log record (plus
KillSwitchService's audit_event) is itself the source of truth, and having the main process
detect that fact and re-publish it is the job of a separate leaf (an outbox poller).
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH
from src.core.safety.split_brain import CheckFn, Diagnosis, SplitBrainDiagnostics
from src.core.safety.watchdog import (
    DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    WatchdogAction,
    WatchdogDecision,
    WatchdogService,
    decide,
)
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.paper_control.adapters.postgres_repository import PostgresPaperControlRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0  # Draft — the interval specified in the original FD-9.1 text

# Must match the system actor row seeded by e5a8c5d4f6b7_liquidation_request.py
# — GLOBAL activate() requires a real users FK, but this decision is grounded
# in the system as a whole rather than any single tenant, so there is no
# tenant to borrow from (see the migration docstring).
WATCHDOG_SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def build_kill_switch_service(pool: asyncpg.Pool) -> KillSwitchService:
    return KillSwitchService(
        risk_gate_repo=PostgresRiskGateRepository(pool),
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={},
        audit_repo=PostgresAuditEventRepository(pool),
    )


class _LastAppliedAction:
    """In-process state that prevents creating a new control (fence++) every cycle (5s) just
    because the same decision repeats. Resets on returning to NORMAL (activate() is designed to
    always succeed on every call, so dedup is this process's responsibility)."""

    def __init__(self) -> None:
        self.value: WatchdogAction = WatchdogAction.NORMAL


async def _apply_decision(
    pool: asyncpg.Pool, decision: WatchdogDecision, kill_switch: KillSwitchService
) -> None:
    """Control creation is delegated entirely to `KillSwitchService.activate` (DoD(f)). Only for
    LIQUIDATE, its result (control id/fence_token) is used to INSERT a `liquidation_request` row
    as REQUESTED (§4 lines 426-430) — since activate() commits its own transaction before
    returning (§5 "transaction boundary"; wrapping it while holding a connection would deadlock,
    P1), the two INSERTs are separate transactions (a crash in between is a known residual risk).
    Unlike a fan-out failure, a failure of this INSERT is core to the decision's outcome, so it is
    not swallowed."""
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


class _LatestExchangeHealth:
    """Addresses red-team audit finding #06 — calls check_exchange() exactly once per cycle and
    caches the result so take_snapshot()'s health_check and split_brain.diagnose() can reuse it
    within the same cycle (previously called twice per cycle, redundantly)."""

    def __init__(self) -> None:
        self.value = False

    async def get(self) -> bool:
        return self.value


async def run_one_cycle(
    pool: asyncpg.Pool,
    service: WatchdogService,
    split_brain: SplitBrainDiagnostics,
    *,
    check_exchange: CheckFn,
    check_db: CheckFn,
    exchange_health_cache: _LatestExchangeHealth,
    kill_switch: KillSwitchService,
    last_action: _LastAppliedAction,
) -> None:
    """One cycle (exchange health check -> snapshot -> Split-Brain diagnosis -> decision ->
    conditional action) — extracted from run_forever's loop body (a pure refactor, to make it
    testable). exchange_healthy is not an input to decide()'s judgment (HALT/LIQUIDATE/NORMAL
    only look at loss_pct and unresponsive_sec) — judging exchange responsiveness is entirely
    Split-Brain's job."""
    exchange_health_cache.value = await check_exchange()
    snapshot = await service.take_snapshot()
    failure_domain = await split_brain.diagnose(
        check_exchange=exchange_health_cache.get,
        check_db=check_db,
        main_process_ok_raw=snapshot.unresponsive_sec < DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    )
    decision = decide(snapshot, market_wide_correlated=None, failure_domain=failure_domain)
    logger.info(
        "Watchdog snapshot=%s decision=%s failure_domain=%s", snapshot, decision, failure_domain
    )
    if failure_domain.diagnosis == Diagnosis.DB_ISOLATED_FAILURE:
        # Per the original FD-9.3 text — a DB-only isolated failure is excluded from forced
        # liquidation, only holding new orders back (no forced action beyond that).
        logger.warning(
            "Split-Brain: DB 단독 장애로 진단 — Watchdog 강제조치 보류 "
            "(신규주문만 자연히 막힘, 강제청산 미실행)"
        )
        # Reset since no action was applied — otherwise, once the DB-only
        # failure clears, the same action would be mistaken for "already
        # applied" and the real decision would be silently skipped.
        last_action.value = WatchdogAction.NORMAL
    elif decision.action != WatchdogAction.NORMAL and decision.action != last_action.value:
        await _apply_decision(pool, decision, kill_switch)
        last_action.value = decision.action
    else:
        last_action.value = decision.action


async def compute_system_equity(pool: asyncpg.Pool) -> Decimal:
    """See the module docstring's deviation note — sums (allocated_capital + closed realized_pnl)
    across RUNNING executions as a system-wide equity approximation (grouped to one row per
    execution after the LEFT JOIN, to avoid double-counting allocated_capital)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.allocated_capital, COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl
            FROM strategy_executions e
            LEFT JOIN positions p ON p.execution_id = e.id
            WHERE e.status = 'RUNNING'
            GROUP BY e.id
            """
        )
    return sum((row["allocated_capital"] + row["realized_pnl"] for row in rows), Decimal("0"))


async def run_forever(pool: asyncpg.Pool) -> None:
    exchange_probe = BitgetAdapter("", "", "", demo_mode=True)

    async def compute_equity() -> Decimal:
        return await compute_system_equity(pool)

    async def check_exchange() -> bool:
        try:
            await exchange_probe.get_ticker("BTC/USDT")
        except Exception:  # noqa: BLE001 — no response = suspected failure, never optimistically treated as True
            return False
        return True

    async def check_db() -> bool:
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:  # noqa: BLE001
            return False
        return True

    exchange_health_cache = _LatestExchangeHealth()

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=DEFAULT_HEARTBEAT_PATH,
    )
    split_brain = SplitBrainDiagnostics()
    kill_switch = build_kill_switch_service(pool)
    last_action = _LastAppliedAction()

    try:
        while True:
            await run_one_cycle(
                pool,
                service,
                split_brain,
                check_exchange=check_exchange,
                check_db=check_db,
                exchange_health_cache=exchange_health_cache,
                kill_switch=kill_switch,
                last_action=last_action,
            )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await exchange_probe.aclose()


async def main() -> None:
    secrets = load_env_secrets()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url.get_secret_value()))
    try:
        await run_forever(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
