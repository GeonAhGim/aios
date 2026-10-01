"""LB-17 `PositionsScheduler` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-17.
DoD(task-727): "스케줄 1주기 실행 후 게이지·스냅샷이 갱신됨을 실제로
단언한다" — sleep 기반 타이밍 대신 `asyncio.Event`로 결정론화한다
(`tests/unit/core/test_base_loop.py` 선례, task-409). 마크가격/FX는 이
리프의 관심사가 아니므로(LB-14가 이미 실DB로 검증) in-memory fake로
대역하고, `pos_snapshot`은 실제 Postgres를 쓴다.

이 파일은 기본 마크 사이클 계약(결정론적 1주기 실행, 계좌별 마크 실패
격리)만 다룬다. reconcile 격리·동시성·게이트 적색 재현은
`test_positions_scheduler_isolation.py`, 수치 성능 예산은
`test_positions_scheduler_perf.py` — 대역/헬퍼는 `scheduler_test_doubles.py`
공용(RATCHET-split, task-10203).
"""

from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal

import pytest

from src.core.observability.metric_names import (
    POSITIONS_SCHEDULER_CYCLE_FAILURE_COUNT_TOTAL,
    POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE,
)
from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.scheduler import (
    CycleReport,
    PositionsScheduler,
    TrackedAccount,
)
from tests.integration.foundation.positions.scheduler_test_doubles import (
    BITGET,
    NOW,
    FailingVenueMarkSource,
    FakeFxRateSource,
    FakeMarkPriceSource,
    clock,
    open_position,
    setup_account,
)


async def test_run_mark_forever_updates_snapshot_and_gauge_deterministically(pool):
    """DoD #5: sleep 폴링 대신 `asyncio.Event`로 "1주기 실행 후" 시점을
    결정론적으로 잡는다(task-409 선례)."""
    tenant_id, account_id = await setup_account(pool)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        marks=FakeMarkPriceSource(price=Money(amount=Decimal("65000"), currency=Currency.USDT)),
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=BITGET,
            )
        ],
        mark_interval_seconds=0.01,
        clock=clock,
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
    assert snapshot.mark_at == NOW
    assert registry.gauge(POSITIONS_SCHEDULER_CYCLE_SUCCESS_GAUGE).samples() == {(): 1.0}


async def test_one_account_mark_failure_does_not_block_another(pool):
    """DoD #3 negative: 한 계좌의 마크 실패가 나머지 계좌를 막지 않고
    실패 카운터만 올린다."""
    failing_tenant, failing_account = await setup_account(pool)
    await open_position(
        pool,
        tenant_id=failing_tenant,
        account_id=failing_account,
        quantity=Decimal("1"),
        venue="FAIL",
    )
    healthy_tenant, healthy_account = await setup_account(pool)
    await open_position(
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
                tenant_id=failing_tenant,
                account_id=failing_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
            ),
            TrackedAccount(
                tenant_id=healthy_tenant,
                account_id=healthy_account,
                base_currency=Currency.USDT,
                calendar=BITGET,
            ),
        ],
        clock=clock,
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


async def test_run_mark_cycle_without_tracked_accounts_skips_wiring_assertion(pool):
    """negative: `tracked=()`면 `marks`/`fx`가 둘 다 배선되지 않아도
    (scheduler.py 모듈독스트링 "tracked=()면 어느 사이클도 이 값들을
    참조하지 않는다") 예외 없이 빈 리포트를 반환한다."""
    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        tracked=(),
        clock=clock,
    )

    report = await scheduler.run_mark_cycle()

    assert report.succeeded == []
    assert report.failed == {}


async def test_run_mark_cycle_with_tracked_accounts_but_unwired_marks_fails_closed(pool):
    """negative: `tracked`가 채워졌는데 `marks`가 배선되지 않으면 조용한
    `AttributeError` 대신 즉시 `AssertionError`로 fail-closed한다
    (scheduler.py 모듈독스트링 "각 사이클 진입 시 assert로 필요한 의존성이
    빠졌는지 바로 드러난다")."""
    tenant_id, account_id = await setup_account(pool)

    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        fx=FakeFxRateSource(),
        tracked=[
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=BITGET,
            )
        ],
        clock=clock,
    )

    with pytest.raises(AssertionError, match="marks/fx"):
        await scheduler.run_mark_cycle()


async def test_run_mark_forever_survives_cycle_exception_and_retries_next_interval(
    pool, monkeypatch
):
    """실패주입: `run_mark_cycle`이 1회차에 예외를 던져도
    `run_mark_forever`의 try/except가 삼키고 다음 주기에 재시도한다 — 사이클
    전체가 죽지 않고 2회차가 실제로 실행됨을 `asyncio.Event`로 결정론적으로
    증명한다(sleep 폴링 대신, task-409 선례)."""
    registry = MetricsRegistry()
    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=registry,
        tracked=(),
        mark_interval_seconds=0.01,
        clock=clock,
    )

    calls = 0
    second_call = asyncio.Event()

    async def _flaky_cycle(self: PositionsScheduler) -> CycleReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("boom")
        second_call.set()
        return CycleReport()

    monkeypatch.setattr(PositionsScheduler, "run_mark_cycle", _flaky_cycle)

    task = asyncio.create_task(scheduler.run_mark_forever())
    await asyncio.wait_for(second_call.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls >= 2
