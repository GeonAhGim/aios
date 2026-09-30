"""LB-17 `PositionsScheduler` 수치 성능 예산 통합테스트 — 실 DB 대상.

DEEPEN(task-2983, docs/audit/DEPTH_LA_LB_LC.md#727)이 추가한 수치 성능
단언. 기본 마크 사이클 계약은 `test_positions_scheduler.py`, 계좌별
격리 negative/동시성/게이트 적색 재현은
`test_positions_scheduler_isolation.py` — 대역/헬퍼는
`scheduler_test_doubles.py` 공용(RATCHET-split, task-10203).
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.scheduler import (
    MARK_INTERVAL_SECONDS,
    PositionsScheduler,
    TrackedAccount,
)
from tests.integration.foundation.positions.scheduler_test_doubles import (
    BITGET,
    FakeFxRateSource,
    FakeMarkPriceSource,
    clock,
    open_position,
    setup_account,
)


@pytest.mark.perf
async def test_run_mark_cycle_completes_within_polling_interval_budget(pool):
    """수치 성능 단언: `run_mark_cycle()`은 폴링 주기
    (`MARK_INTERVAL_SECONDS`, §2.3 Draft 10s)마다 추적 계좌 전체를 한 번씩
    순회한다 — 계좌 수가 늘어도 그 사이클 자체가 다음 폴링 간격을 밀어낼
    만큼 오래 걸리면 안 된다. N=15개 계좌(각 1포지션, in-memory
    `FakeMarkPriceSource`로 네트워크 없이 DB 왕복만 측정)를 그 예산 안에
    처리함을 실측한다."""
    n = 15
    tracked: list[TrackedAccount] = []
    for _ in range(n):
        tenant_id, account_id = await setup_account(pool)
        await open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))
        tracked.append(
            TrackedAccount(
                tenant_id=tenant_id,
                account_id=account_id,
                base_currency=Currency.USDT,
                calendar=BITGET,
            )
        )

    scheduler = PositionsScheduler(
        pool,
        snapshots=PostgresSnapshotRepository(pool),
        registry=MetricsRegistry(),
        marks=FakeMarkPriceSource(price=Money(amount=Decimal("65000"), currency=Currency.USDT)),
        fx=FakeFxRateSource(),
        tracked=tracked,
        clock=clock,
    )

    budget_sec = MARK_INTERVAL_SECONDS
    start = time.perf_counter()
    report = await scheduler.run_mark_cycle()
    elapsed = time.perf_counter() - start

    print(f"[LB-17 run_mark_cycle] n={n} elapsed={elapsed:.3f}s (budget<{budget_sec}s)")
    assert report.succeeded == [target.account_id for target in tracked]
    assert elapsed < budget_sec, (
        f"run_mark_cycle {n}개 계좌 처리가 폴링 간격 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) -- 다음 주기를 밀어낼 수 있습니다."
    )
