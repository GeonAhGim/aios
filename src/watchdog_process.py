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

import asyncpg

from src.core.loader.secret_loader import load_env_secrets
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH
from src.core.safety.market_correlation import is_market_wide_move
from src.core.safety.split_brain import CheckFn, Diagnosis, SplitBrainDiagnostics
from src.core.safety.watchdog import (
    DEFAULT_LOSS_THRESHOLD_PCT,
    DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    WatchdogAction,
    WatchdogService,
    decide,
)
from src.core.safety.watchdog_basket import (
    MARKET_WIDE_MOVE_THRESHOLD_PCT,
    GetBasketReturnsFn,
    get_basket_returns,
)
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.paper_control.adapters.postgres_repository import PostgresPaperControlRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.safety.kill_switch_service import KillSwitchService
from src.services.safety.watchdog_apply import (  # noqa: F401 — re-exported for tests/callers
    WATCHDOG_SYSTEM_ACTOR_ID,
    apply_decision,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0  # Draft — the interval specified in the original FD-9.1 text


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
    get_basket_returns: GetBasketReturnsFn,
) -> None:
    """One cycle (exchange health check -> snapshot -> Split-Brain diagnosis -> decision ->
    conditional action) — extracted from run_forever's loop body (a pure refactor, to make it
    testable). exchange_healthy is not an input to decide()'s judgment (HALT/LIQUIDATE/NORMAL
    only look at loss_pct and unresponsive_sec) — judging exchange responsiveness is entirely
    Split-Brain's job.

    `get_basket_returns` (RTF-03) is only invoked once loss_pct has already crossed the
    LIQUIDATE/HALT threshold — `market_wide_correlated` is otherwise unused by `decide()`,
    so fetching the basket every 5s cycle regardless would be a needless exchange-API call
    rate."""
    exchange_health_cache.value = await check_exchange()
    snapshot = await service.take_snapshot()
    failure_domain = await split_brain.diagnose(
        check_exchange=exchange_health_cache.get,
        check_db=check_db,
        main_process_ok_raw=snapshot.unresponsive_sec < DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    )
    market_wide_correlated: bool | None = None
    if snapshot.loss_pct >= DEFAULT_LOSS_THRESHOLD_PCT:
        basket_returns = await get_basket_returns()
        market_wide_correlated = is_market_wide_move(
            basket_returns,
            account_loss_pct=snapshot.loss_pct,
            move_threshold_pct=MARKET_WIDE_MOVE_THRESHOLD_PCT,
        )
    decision = decide(
        snapshot, market_wide_correlated=market_wide_correlated, failure_domain=failure_domain
    )
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
        await apply_decision(pool, decision, kill_switch)
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

    async def get_basket_returns_cb() -> dict[str, Decimal]:
        return await get_basket_returns(exchange_probe)

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
                get_basket_returns=get_basket_returns_cb,
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
