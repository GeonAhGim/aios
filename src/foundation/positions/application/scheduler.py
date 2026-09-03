"""LB-17 — positions 스케줄러: 마크(Draft 10s)·대사(Draft 60s)·
NAV(세션 마감 +5m) 주기 실행.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §7, §9.3 LB-17.
간격 값은 새로 만들지 않고 §2.3 `application/scheduler.py` 행의 Draft
숫자를 그대로 쓴다.

편차: 명세 표는 자유 함수 `run_positions_scheduler(app_state, *, stop)`로
적지만, 이미 병합된 같은 계층의 스케줄러 셋(`execution_loop`, `ledger`,
`market_data`)이 전부 클래스 + `run_forever()` 메서드 패턴이라 그쪽을
따른다(`market_data/application/scheduler.py`의 같은 판단, task-712 decision).

계좌 단위 예외 격리: 세 단계(mark/reconcile/nav) 전부 계좌 하나의 실패를
잡아 실패 카운터 메트릭(`POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL`)만
올리고 다음 계좌로 진행한다 — 한 계좌의 예외가 사이클 전체를 막지 않는다
(§9 LB-17 DoD, `reconcile_provider.py`의 계좌별 독립 호출과 같은 원칙).

일별 NAV 롤포워드(realized/unrealized_delta/funding/fees/flows)를 저널에서
집계하는 로직은 아직 없다 — `compute_daily_nav.py` 모듈독스트링이 "다음
리프가 발명하지 않도록" 남겨 둔 부분(task-714 decision)이고, 이 스케줄러도
새로 발명하지 않는다. 대신 `compute_daily_nav`의 `CashSource`와 같은 방식으로
`DailyRollForward`를 호출자가 주입하게 한다 — `TrackedAccount.roll_forward`가
없는 계좌는 NAV 단계를 건너뛴다. 운영 배선(저널 집계 구현·계좌 목록·거래소
연결)은 §10 후속 과제로 남는다: `main.py`는 `tracked=()`로 배선해 이
스케줄러가 실제로는 아무 계좌도 처리하지 않는다(`market_data`
`MarketDataQualityScheduler`의 `watched=()` 선례와 동일, LA-18 task-712).

`marks`/`fx`/`nav_repo`/`cash`/`provider`/`recon`은 그래서 전부 선택
인자(기본값 `None`)다 — `tracked=()`면 어느 사이클도 이 값들을 참조하지
않으므로 `main.py`가 아직 존재하지 않는 어댑터(`CashSource`, `main.py`
자체에 실제 계좌·거래소 연결 레지스트리)를 억지로 만들어 넘길 필요가
없다. `tracked`가 실제로 채워지면 각 사이클 진입 시 `assert`로 필요한
의존성이 빠졌는지 바로 드러난다(조용한 `AttributeError` 대신).

NAV는 매 주기(폴링 간격 `NAV_POLL_INTERVAL_SECONDS`) `nav_repo.get`으로
그날 이미 계산됐는지 먼저 확인해 멱등하게 건너뛴다 — 하루 한 번만 의미
있는 이벤트를 정확한 시각에 깨우는 스케줄 대신, 이미 있는 값이면 다시
계산하지 않는 폴링으로 단순화했다(`compute_daily_nav` 자체도 같은 날
재계산에 `source_hash` 비교로 멱등하므로 이중 방어).
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
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
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
    """일별 NAV 롤포워드 입력(저널 집계, 아직 구현 없음 — 모듈독스트링
    참조). 호출자가 이미 계산해 둔 값을 준다는 계약만 표현한다."""

    async def roll_forward(self, account_id: UUID, at: datetime) -> RollForwardValues: ...


@dataclass(frozen=True, slots=True)
class TrackedAccount:
    """스케줄러가 매 주기 처리할 계좌 하나. `connection_id`가 없으면 대사
    단계를, `roll_forward`가 없으면 NAV 단계를 건너뛴다."""

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
            "positions_scheduler: account_id=%s stage=%s 실패 — 다음 주기에 재시도",
            account_id, stage,
        )

    async def run_mark_cycle(self) -> CycleReport:
        report = CycleReport()
        if not self._tracked:
            return report
        assert self._marks is not None and self._fx is not None, "marks/fx 미배선(tracked 있음)"
        for target in self._tracked:
            try:
                await mark_positions(
                    target.tenant_id, target.account_id,
                    snapshots=self._snapshots, marks=self._marks, fx=self._fx,
                    pool=self._pool, clock=self._clock,
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
            "provider/recon 미배선(tracked 있음)"
        )
        for target in self._tracked:
            if target.connection_id is None:
                continue
            try:
                await reconcile_account(
                    target.tenant_id, target.account_id,
                    connection_id=target.connection_id, snapshots=self._snapshots,
                    provider=self._provider, recon=self._recon, pool=self._pool,
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
        assert (
            self._nav_repo is not None and self._cash is not None and self._fx is not None
        ), "nav_repo/cash/fx 미배선(tracked 있음)"
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
                    cmd, snapshots=self._snapshots, cash=self._cash, nav_repo=self._nav_repo,
                    calendar=target.calendar, fx=self._fx, pool=self._pool,
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
                logger.exception("positions_scheduler: mark 사이클 전체 실패 — 다음 주기에 재시도")

    async def run_reconcile_forever(self) -> None:
        while True:
            await asyncio.sleep(self.reconcile_interval_seconds)
            try:
                await self.run_reconcile_cycle()
            except Exception:
                logger.exception(
                    "positions_scheduler: reconcile 사이클 전체 실패 — 다음 주기에 재시도"
                )

    async def run_nav_forever(self) -> None:
        while True:
            await asyncio.sleep(self.nav_poll_interval_seconds)
            try:
                await self.run_nav_cycle()
            except Exception:
                logger.exception("positions_scheduler: nav 사이클 전체 실패 — 다음 주기에 재시도")
