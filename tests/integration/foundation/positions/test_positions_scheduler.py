"""LB-17 `PositionsScheduler` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-17.
DoD(task-727): "스케줄 1주기 실행 후 게이지·스냅샷이 갱신됨을 실제로
단언한다" — sleep 기반 타이밍 대신 `asyncio.Event`로 결정론화한다
(`tests/unit/core/test_base_loop.py` 선례, task-409). 마크가격/FX는 이
리프의 관심사가 아니므로(LB-14가 이미 실DB로 검증) in-memory fake로
대역하고, `pos_snapshot`은 실제 Postgres를 쓴다.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.core.observability.metric_names import (
    POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL,
    POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.scheduler import PositionsScheduler, TrackedAccount
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


def _clock() -> datetime:
    return _NOW


class FakeMarkPriceSource:
    def __init__(self, price: Money | None = None) -> None:
        self._price = price

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        return self._price


class FailingVenueMarkSource:
    """`venue == "FAIL"`인 포지션만 예외를 던지는 대역 — 계좌 하나의
    실패를 재현하려고 그 계좌의 포지션만 이 venue로 연다."""

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        if PositionKey.parse(position_key).venue == "FAIL":
            raise ConnectionError("boom")
        return Money(amount=Decimal("70000"), currency=Currency.USDT)


class FakeFxRateSource:
    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError("이 스위트는 통화 불일치를 쓰지 않는다")


def _unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


async def _open_position(
    pool, *, tenant_id, account_id, quantity: Decimal, venue: str = "bitget"
) -> PositionSnapshotView:
    position_key = str(
        PositionKey(
            venue=venue, instrument_id=_unique_symbol("BTCUSDT"), strategy_id="default",
            execution_id="paper",
        )
    )
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=_NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _setup_account(pool) -> tuple:
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id


async def test_run_mark_forever_updates_snapshot_and_gauge_deterministically(pool):
    """DoD #5: sleep 폴링 대신 `asyncio.Event`로 "1주기 실행 후" 시점을
    결정론적으로 잡는다(task-409 선례)."""
    tenant_id, account_id = await _setup_account(pool)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=FakeMarkPriceSource(price=Money(amount=Decimal("65000"), currency=Currency.USDT)),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=tenant_id, account_id=account_id, base_currency=Currency.USDT,
                calendar=_BITGET,
            )
        ],
        mark_interval_seconds=0.01,
        clock=_clock,
    )

    ran_once = asyncio.Event()
    original_cycle = scheduler.run_mark_cycle

    async def _tracked_cycle():
        report = await original_cycle()
        ran_once.set()
        return report

    scheduler.run_mark_cycle = _tracked_cycle  # type: ignore[method-assign]

    task = asyncio.create_task(scheduler.run_mark_forever())
    await asyncio.wait_for(ran_once.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    async with pool.acquire() as conn:
        [snapshot] = await PostgresSnapshotRepository(pool).list_open(conn, tenant_id, account_id)
    assert snapshot.mark_price == Money(amount=Decimal("65000"), currency=Currency.USDT)
    assert snapshot.mark_at == _NOW
    assert registry.gauge(POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE).samples() == {(): 1.0}


async def test_one_account_mark_failure_does_not_block_another(pool):
    """DoD #3 negative: 한 계좌의 마크 실패가 나머지 계좌를 막지 않고
    실패 카운터만 올린다."""
    failing_tenant, failing_account = await _setup_account(pool)
    await _open_position(
        pool, tenant_id=failing_tenant, account_id=failing_account, quantity=Decimal("1"),
        venue="FAIL",
    )
    healthy_tenant, healthy_account = await _setup_account(pool)
    await _open_position(
        pool, tenant_id=healthy_tenant, account_id=healthy_account, quantity=Decimal("1")
    )

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=FailingVenueMarkSource(),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=failing_tenant, account_id=failing_account,
                base_currency=Currency.USDT, calendar=_BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant, account_id=healthy_account,
                base_currency=Currency.USDT, calendar=_BITGET,
            ),
        ],
        clock=_clock,
    )

    report = await scheduler.run_mark_cycle()

    assert report.succeeded == [healthy_account]
    assert f"{failing_account}:mark" in report.failed
    assert registry.counter(POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL).samples() == {(): 1.0}

    async with pool.acquire() as conn:
        [healthy_snapshot] = await PostgresSnapshotRepository(pool).list_open(
            conn, healthy_tenant, healthy_account
        )
    assert healthy_snapshot.mark_price == Money(amount=Decimal("70000"), currency=Currency.USDT)
