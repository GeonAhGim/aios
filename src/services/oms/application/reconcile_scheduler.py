"""L4-24 — 3-way reconciliation periodic runner.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `reconcile_scheduler.py`,
§9 L4-24, §5.1 (advisory lock, REC-004).

DoD (d) "do not reinvent a new resident-loop framework" — follows the same
already-proven pattern as `OutboxDispatcher.run_forever` (L4-14): "sleep->tick
forever, log a single cycle's exception and keep going". Since this leaf only
calls `three_way_reconciler.reconcile_account` (see that module's docstring —
scope reduced due to the absence of an internal balance/position ledger),
which compares orders only, it keeps a single orders cadence (5 minutes). The
balance/position 15-minute cadence (§2-C table) will be added once that ledger
exists.

Actually registering the background task (`background_loops.py`) is out of
this leaf's scope — for the same reason as `wiring.py`'s
`start_bitget_private_ws_inbox_task` (L4-20) (the decision that "wiring.py
changes are limited to a single registration assembly function"; a follow-up
leaf actually attaches it as a task in main.py/background_loops.py).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import asyncpg

from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.services.oms.application.three_way_reconciler import DEFAULT_POLICY, reconcile_account

logger = logging.getLogger(__name__)

DEFAULT_ORDER_WINDOW = timedelta(minutes=5)
DEFAULT_INTERVAL_SEC = 300.0  # §2-C "orders every 5 minutes" cadence


@dataclass(frozen=True)
class ReconcileTarget:
    tenant_id: UUID
    connection_id: UUID | None
    account_ref: str
    adapter: ExchangeAdapter


TargetProvider = Callable[[], Awaitable[Sequence[ReconcileTarget]]]


class ReconcileScheduler:
    """Per-target `pg_try_advisory_xact_lock(hashtext('recon:'||account_ref))` —
    prevents multiple scheduler instances/manual runs from reconciling the same
    account concurrently (REC-004 "skip on conflict"). The lock is released
    automatically when the transaction wrapping that single reconciliation ends
    (advisory **xact** lock) — there is no separate release call."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        targets: TargetProvider,
        window: timedelta = DEFAULT_ORDER_WINDOW,
        policy: MaterialityPolicy = DEFAULT_POLICY,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._targets = targets
        self._window = window
        self._policy = policy
        self._interval_sec = interval_sec
        self._sleep = sleep

    async def tick(self) -> int:
        """One cycle — attempts to reconcile every target. The return value is
        the count of targets that actually acquired the lock and were
        reconciled (excludes ones skipped due to lock-acquisition failure;
        for observability)."""
        reconciled = 0
        for target in await self._targets():
            if await self._reconcile_one(target):
                reconciled += 1
        return reconciled

    async def _reconcile_one(self, target: ReconcileTarget) -> bool:
        async with self._pool.acquire() as conn, conn.transaction():
            acquired = await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtext('recon:' || $1))", target.account_ref
            )
            if not acquired:
                logger.info(
                    "reconcile_scheduler: account_ref=%s 이미 대사 중 — 이번 주기 건너뜁니다"
                    "(REC-004).",
                    target.account_ref,
                )
                return False
            try:
                summary = await reconcile_account(
                    pool=self._pool,
                    adapter=target.adapter,
                    tenant_id=target.tenant_id,
                    connection_id=target.connection_id,
                    account_ref=target.account_ref,
                    window=self._window,
                    policy=self._policy,
                )
            except Exception:
                logger.exception(
                    "reconcile_scheduler: account_ref=%s 대사 실패 — 다음 주기에 재시도",
                    target.account_ref,
                )
                return False
            if summary.overall_classification != "HEALTHY":
                logger.warning(
                    "reconcile_scheduler: account_ref=%s classification=%s discrepancies=%d",
                    target.account_ref,
                    summary.overall_classification,
                    len(summary.discrepancies),
                )
        return True

    async def run_forever(self) -> None:
        """Same principle as `outbox_dispatcher.run_forever` — a whole cycle's
        failure does not kill the loop itself."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("reconcile_scheduler: 이번 주기 전체 실패 — 다음 주기에 재시도")
            await self._sleep(self._interval_sec)
