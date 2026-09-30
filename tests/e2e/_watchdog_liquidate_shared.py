"""E2E-2 워치독 LIQUIDATE 테스트 스위트 공용 픽스처/헬퍼.

`test_e2e_2_watchdog_liquidate.py`(본선 경로)와
`test_e2e_2_watchdog_liquidate_deepen.py`(DEEPEN -- negative/실패주입/성능)가
공유한다. 두 파일 모두 500줄 임계에 근접하는 것을 피하기 위해 분리했다
(CLAUDE.md §9 File policy -- 책임별 분할, 라인 수가 아니라)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogService
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _LastAppliedAction,
    _LatestExchangeHealth,
    build_kill_switch_service,
    run_one_cycle,
)
from tests.support.db import create_pool_with_retry


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


# tests/e2e/conftest.py의 `pool`을 그대로 쓴다 (min/max_size 조정이 필요 없다 --
# 이 스위트는 동시성 부하 테스트가 아니다).


@pytest.fixture
def kill_switch(pool):
    return build_kill_switch_service(pool)


@pytest.fixture(scope="module", autouse=True)
async def _clear_stale_liquidation_requests():
    """`_select_candidate`의 SELECT...FOR UPDATE SKIP LOCKED LIMIT 1은 전역에서
    가장 오래된 non-terminal 행을 고른다 -- 같은 공유 TEST_DATABASE_URL의 다른
    스위트가 남긴 REQUESTED 행이 있으면 이 모듈의 워커 호출이 그걸 훔쳐간다
    (test_liquidation_worker.py / test_watchdog_market_wide_liquidation_e2e.py와
    동일 관례)."""
    pool = await create_pool_with_retry(_asyncpg_dsn(), min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state='ABORTED', completed_at=now() "
            "WHERE state IN ('REQUESTED','PLANNED','EXECUTING')"
        )
    await pool.close()
    yield


@pytest.fixture(autouse=True)
async def _cleanup_watchdog_state(pool):
    """이 스위트가 만드는 GLOBAL safety_control/liquidation_request가 공유
    TEST_DATABASE_URL의 다른 risk-gate 테스트를 오염시키지 않도록,
    WATCHDOG_SYSTEM_ACTOR_ID로 만들어진 것만 정리한다."""
    yield
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state = 'ABORTED', completed_at = now() "
            "WHERE safety_control_id IN "
            "(SELECT id FROM safety_control WHERE actor_subject_id = $1) "
            "AND state IN ('REQUESTED', 'PLANNED', 'EXECUTING')",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )


async def _insert_open_position(
    pool: asyncpg.Pool, user_id: UUID, symbol: str, *, quantity: Decimal = Decimal("1")
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', $3, 50000, now())",
            user_id,
            symbol,
            quantity,
        )


async def _latest_liquidation_request(
    pool: asyncpg.Pool, *, since: datetime
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT lr.* FROM liquidation_request lr "
            "JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1 AND lr.requested_at > $2 "
            "ORDER BY lr.requested_at DESC LIMIT 1",
            WATCHDOG_SYSTEM_ACTOR_ID,
            since,
        )


async def _market_wide_basket() -> dict[str, Decimal]:
    # 4개 중 3개(과반)가 임계(3%)를 넘겨 하락 -- 시장 전체 급변으로 판정돼야 한다.
    return {
        "BTC/USDT": Decimal("-9"),
        "ETH/USDT": Decimal("-8"),
        "SOL/USDT": Decimal("-9"),
        "XRP/USDT": Decimal("0.1"),
    }


async def _run_liquidate_cycle(
    pool: asyncpg.Pool,
    kill_switch,
    *,
    heartbeat_path,
    check_exchange,
    check_db,
    split_brain: SplitBrainDiagnostics | None = None,
    get_basket_returns=None,
    equity_readings: list[Decimal] | None = None,
) -> asyncpg.Record:
    """LIQUIDATE 발동에 필요한 최소 2사이클(peak 기록 -> 급락 판정)을 돌리고,
    이번 테스트가 만든 liquidation_request 행을 반환한다."""
    write_heartbeat(heartbeat_path)
    readings = iter(equity_readings or [Decimal("10000"), Decimal("1000")])  # peak -> 90% 손실

    async def compute_equity() -> Decimal:
        return next(readings)

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat_path,
    )
    split_brain = split_brain or SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    cycle_kwargs = {
        "check_exchange": check_exchange,
        "check_db": check_db,
        "exchange_health_cache": exchange_health_cache,
        "kill_switch": kill_switch,
        "last_action": last_action,
        "get_basket_returns": get_basket_returns or _market_wide_basket,
    }

    test_start = datetime.now(timezone.utc)
    # 1회차 -- peak 기록, 손실률 0% -> NORMAL.
    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)
    # 2회차 -- 90% 손실 + market-wide 급변 basket -> LIQUIDATE 발동(조건에 따라).
    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)

    return await _latest_liquidation_request(pool, since=test_start)
