"""LB-17 — positions scheduler: runs mark (Draft 10s), reconcile
(Draft 60s), and NAV (session close +5m) cycles.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §7, §9.3 LB-17.
The interval values are not invented here; they are taken verbatim from the
Draft numbers in the §2.3 `application/scheduler.py` row.

Deviation: the spec table writes this as a free function
`run_positions_scheduler(app_state, *, stop)`, but every already-merged
scheduler at this same layer (`execution_loop`, `ledger`, `market_data`)
uses the class + `run_forever()` method pattern, so this follows that
instead (same call as `market_data/application/scheduler.py`, task-712
decision).

Per-account exception isolation: all three stages (mark/reconcile/nav)
catch a single account's failure, only bump the failure counter metric
(`POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL`), and move on to the next
account — one account's exception never blocks the whole cycle (§9 LB-17
DoD, same principle as the per-account independent calls in
`reconcile_provider.py`).

The logic to aggregate the daily NAV roll-forward
(realized/unrealized_delta/funding/fees/flows) from the journal does not
exist yet — `compute_daily_nav.py`'s module docstring deliberately leaves
this for "the next leaf to invent" (task-714 decision), and this scheduler
does not invent it either. Instead, following the same approach as
`compute_daily_nav`'s `CashSource`, the caller injects a `DailyRollForward`
— an account without `TrackedAccount.roll_forward` simply skips the NAV
stage. Operational wiring (the journal-aggregation implementation, the
account list, exchange connections) is left as a §10 follow-up: `main.py`
wires this with `tracked=()`, so this scheduler currently processes no
accounts at all (same precedent as `market_data`'s
`MarketDataQualityScheduler` using `watched=()`, LA-18 task-712).

That is why `marks`/`fx`/`nav_repo`/`cash`/`provider`/`recon` are all
optional arguments (default `None`) — with `tracked=()` no cycle ever
references these values, so there is no need to force `main.py` to build
and pass adapters that do not exist yet (`CashSource`, or a real
account/exchange connection registry inside `main.py` itself). Once
`tracked` is actually populated, an `assert` at the start of each cycle
immediately surfaces any missing dependency (instead of a silent
`AttributeError`).

NAV uses `nav_repo.get` on every cycle (poll interval
`NAV_POLL_INTERVAL_SECONDS`) to check whether it has already been computed
for the day, and skips idempotently if so — instead of a schedule that
wakes precisely at an event meaningful only once a day, this is simplified
to polling that skips recomputation when a value already exists
(`compute_daily_nav` itself is also idempotent for same-day recomputation
via a `source_hash` comparison, so this is defense in depth).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple, Protocol, runtime_checkable
from uuid import UUID, uuid4

import asyncpg

from src.core.observability.metric_names import (
    POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL,
    POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency
from src.foundation.market_data.api import VenueCalendar
from src.foundation.positions.application.compute_daily_nav import (
    CashSource,
    ComputeDailyNavCommand,
    compute_daily_nav,
)
from src.foundation.positions.application.mark_positions import mark_positions
from src.foundation.positions.application.reconcile_provider import (
    RunReconciliation,
    reconcile_account,
)
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.mark_price_source import MarkPriceSource
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = ["DailyRollForward", "PositionsScheduler", "RollForwardValues", "TrackedAccount"]

logger = logging.getLogger(__name__)

MARK_INTERVAL_SECONDS = 10.0
RECONCILE_INTERVAL_SECONDS = 60.0
NAV_POLL_INTERVAL_SECONDS = 60.0
NAV_LAG_AFTER_CLOSE = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RollForwardValues(NamedTuple):
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal


@runtime_checkable
class DailyRollForward(Protocol):
    """Daily NAV roll-forward input (journal aggregation, not implemented
    yet — see module docstring). Only expresses the contract that the
    caller provides already-computed values."""

    async def roll_forward(self, account_id: UUID, at: datetime) -> RollForwardValues: ...


@dataclass(frozen=True, slots=True)
class TrackedAccount:
    """One account the scheduler processes each cycle. Skips the reconcile
    stage if `connection_id` is absent, and the NAV stage if `roll_forward`
    is absent."""

    tenant_id: UUID
    account_id: UUID
    base_currency: Currency
    calendar: VenueCalendar
    connection_id: UUID | None = None
    roll_forward: DailyRollForward | None = None


@dataclass
class CycleReport:
    succeeded: list[UUID] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # f"{account_id}:{stage}" -> error


def _today_session_close(calendar: VenueCalendar, now: datetime) -> datetime | None:
    windows = calendar.sessions_for(calendar.trading_day_of(now))
    return windows[0].close_at if windows else None


class PositionsScheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        snapshots: SnapshotRepository,
        registry: MetricsRegistry,
        marks: MarkPriceSource | None = None,
        fx: FxRateSource | None = None,
        nav_repo: NavRepository | None = None,
        cash: CashSource | None = None,
        provider: ProviderBalanceSource | None = None,
        recon: RunReconciliation | None = None,
        tracked: Sequence[TrackedAccount] = (),
        mark_interval_seconds: float = MARK_INTERVAL_SECONDS,
        reconcile_interval_seconds: float = RECONCILE_INTERVAL_SECONDS,
        nav_poll_interval_seconds: float = NAV_POLL_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._pool = pool
        self._snapshots = snapshots
        self._marks = marks
        self._fx = fx
        self._nav_repo = nav_repo
        self._cash = cash
        self._provider = provider
        self._recon = recon
        self._registry = registry
        self._tracked = list(tracked)
        self.mark_interval_seconds = mark_interval_seconds
        self.reconcile_interval_seconds = reconcile_interval_seconds
        self.nav_poll_interval_seconds = nav_poll_interval_seconds
        self._clock = clock

    def _fail(self, report: CycleReport, account_id: UUID, stage: str, exc: Exception) -> None:
        report.failed[f"{account_id}:{stage}"] = f"{type(exc).__name__}: {exc}"
        self._registry.counter(POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL).inc()
        logger.exception(
            "positions_scheduler: account_id=%s stage=%s failed — will retry next cycle",
            account_id,
            stage,
        )

    async def run_mark_cycle(self) -> CycleReport:
        report = CycleReport()
        if not self._tracked:
            return report
        assert self._marks is not None and self._fx is not None, (
            "marks/fx not wired (tracked is non-empty)"
        )
        for target in self._tracked:
            try:
                await mark_positions(
                    target.tenant_id,
                    target.account_id,
                    snapshots=self._snapshots,
                    marks=self._marks,
                    fx=self._fx,
                    pool=self._pool,
                    clock=self._clock,
                )
            except Exception as exc:
                self._fail(report, target.account_id, "mark", exc)
                continue
            report.succeeded.append(target.account_id)
        self._registry.gauge(POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE).set(len(report.succeeded))
        return report

    async def run_reconcile_cycle(self) -> CycleReport:
        report = CycleReport()
        if not self._tracked:
            return report
        assert self._provider is not None and self._recon is not None, (
            "provider/recon not wired (tracked is non-empty)"
        )
        for target in self._tracked:
            if target.connection_id is None:
                continue
            try:
                await reconcile_account(
                    target.tenant_id,
                    target.account_id,
                    connection_id=target.connection_id,
                    snapshots=self._snapshots,
                    provider=self._provider,
                    recon=self._recon,
                    pool=self._pool,
                    registry=self._registry,
                )
            except Exception as exc:
                self._fail(report, target.account_id, "reconcile", exc)
                continue
            report.succeeded.append(target.account_id)
        return report

    async def run_nav_cycle(self) -> CycleReport:
        report = CycleReport()
        if not self._tracked:
            return report
        assert self._nav_repo is not None and self._cash is not None and self._fx is not None, (
            "nav_repo/cash/fx not wired (tracked is non-empty)"
        )
        now = self._clock()
        for target in self._tracked:
            if target.roll_forward is None:
                continue
            close_at = _today_session_close(target.calendar, now)
            if close_at is None or now < close_at + NAV_LAG_AFTER_CLOSE:
                continue
            nav_date = target.calendar.trading_day_of(now)
            async with self._pool.acquire() as conn:
                already_computed = await self._nav_repo.get(conn, target.account_id, nav_date)
            if already_computed is not None:
                continue
            try:
                values = await target.roll_forward.roll_forward(target.account_id, now)
                cmd = ComputeDailyNavCommand(
                    tenant_id=target.tenant_id,
                    account_id=target.account_id,
                    base_currency=target.base_currency,
                    at=now,
                    realized=values.realized,
                    unrealized_delta=values.unrealized_delta,
                    funding=values.funding,
                    fees=values.fees,
                    flows=values.flows,
                    trace_id=uuid4(),
                )
                await compute_daily_nav(
                    cmd,
                    snapshots=self._snapshots,
                    cash=self._cash,
                    nav_repo=self._nav_repo,
                    calendar=target.calendar,
                    fx=self._fx,
                    pool=self._pool,
                )
            except Exception as exc:
                self._fail(report, target.account_id, "nav", exc)
                continue
            report.succeeded.append(target.account_id)
        return report

    async def run_mark_forever(self) -> None:
        while True:
            await asyncio.sleep(self.mark_interval_seconds)
            try:
                await self.run_mark_cycle()
            except Exception:
                logger.exception(
                    "positions_scheduler: mark cycle failed entirely — will retry next cycle"
                )

    async def run_reconcile_forever(self) -> None:
        while True:
            await asyncio.sleep(self.reconcile_interval_seconds)
            try:
                await self.run_reconcile_cycle()
            except Exception:
                logger.exception(
                    "positions_scheduler: reconcile cycle failed entirely — will retry next cycle"
                )

    async def run_nav_forever(self) -> None:
        while True:
            await asyncio.sleep(self.nav_poll_interval_seconds)
            try:
                await self.run_nav_cycle()
            except Exception:
                logger.exception(
                    "positions_scheduler: nav cycle failed entirely — will retry next cycle"
                )
