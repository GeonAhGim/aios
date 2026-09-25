"""RTF-03 종결 E2E — docs/RED_TEAM_FINDINGS.md 2026-09-05-44 잔여 갭.

R-49/R-51/task-2838이 판정기(`is_market_wide_move`)·발동 경로(`liquidation_request`
INSERT)·`failure_domain` 배선까지 고쳤지만, `run_one_cycle`이 `decide(...,
market_wide_correlated=None)`을 계속 하드코딩해 실제 시장 전체 급변 시나리오에서도
LIQUIDATE가 구조적으로 도달 불가했다. 이 파일은 `get_basket_returns` 콜백이
실제 basket 데이터를 `decide()`까지 전달해 (1) `liquidation_request`가
REQUESTED로 INSERT되고 (2) `run_liquidation_worker_once`(실행 루프의 소비자)가
그 행을 실제로 집어 PLANNED로 전이시키는 전체 경로를 실증한다."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogService
from src.services.safety.liquidation_executor import run_liquidation_worker_once
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _LastAppliedAction,
    _LatestExchangeHealth,
    build_kill_switch_service,
    run_one_cycle,
)
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture(scope="module", autouse=True)
async def _clear_stale_requests():
    """`_select_candidate`(liquidation_executor.py)의 SELECT...FOR UPDATE SKIP
    LOCKED LIMIT 1은 전역에서 가장 오래된 non-terminal 행을 고른다(요청 단위
    스코핑 없음) — 같은 공유 TEST_DATABASE_URL의 다른 스위트(예:
    test_watchdog_liquidation_request.py)가 전이시키지 않고 남긴 REQUESTED 행이
    있으면 이 모듈의 `run_liquidation_worker_once` 호출이 그걸 훔쳐가 버린다.
    test_liquidation_worker.py와 동일한 관례."""
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state='ABORTED', completed_at=now() "
            "WHERE state IN ('REQUESTED','PLANNED','EXECUTING')"
        )
    await pool.close()
    yield


@pytest.fixture
def kill_switch(pool):
    return build_kill_switch_service(pool)


@pytest.fixture(autouse=True)
async def _cleanup_watchdog_state(pool):
    """이 파일이 만드는 GLOBAL safety_control/liquidation_request가 공유
    TEST_DATABASE_URL의 다른 risk-gate 테스트를 오염시키지 않도록,
    WATCHDOG_SYSTEM_ACTOR_ID로 만들어진 것만 정리한다 — 기존
    test_watchdog_liquidation_request.py와 동일 관례."""
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


async def _insert_open_position(pool: asyncpg.Pool, user_id: UUID, symbol: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', 1, 50000, now())",
            user_id,
            symbol,
        )


async def _latest_liquidation_request(
    pool: asyncpg.Pool, *, since: datetime
) -> asyncpg.Record | None:
    """WATCHDOG_SYSTEM_ACTOR_ID로 스코프된 liquidation_request는 다른 테스트
    파일(test_watchdog_liquidation_request.py 등)이 같은 공유 TEST_DATABASE_URL에
    남긴 행과 뒤섞인다 — `requested_at > since`(테스트 시작 시각)로 걸러 이 테스트가
    이번에 만든 행만 본다."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT lr.* FROM liquidation_request lr "
            "JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1 AND lr.requested_at > $2 "
            "ORDER BY lr.requested_at DESC LIMIT 1",
            WATCHDOG_SYSTEM_ACTOR_ID,
            since,
        )


async def test_market_wide_correlated_loss_creates_and_the_execution_loop_consumes_request(
    pool, kill_switch, tmp_path
):
    """전체 경로: 시장 전체 급변 basket + 계좌 손실 -> decide()가 실제로
    LIQUIDATE 판정 -> `liquidation_request` REQUESTED INSERT ->
    `run_liquidation_worker_once`(실행 루프)가 그 행을 소비해 PLANNED로 전이."""
    test_start = datetime.now(timezone.utc)
    user_id = await create_test_user(pool)
    symbol = f"WD{uuid4().hex[:10]}/USDT"
    await _insert_open_position(pool, user_id, symbol)

    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)  # 메인 프로세스는 정상 응답 — HALT(unresponsive)가 아니라
    # LIQUIDATE 경로(loss_pct >= threshold)를 타야 한다.

    equity_readings = iter([Decimal("10000"), Decimal("1000")])  # peak -> 90% 손실

    async def compute_equity() -> Decimal:
        return next(equity_readings)

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    async def get_basket_returns() -> dict[str, Decimal]:
        # 4개 중 3개(과반)가 임계(3%)를 넘겨 하락 — 시장 전체 급변으로 판정돼야 한다.
        return {
            "BTC/USDT": Decimal("-9"),
            "ETH/USDT": Decimal("-8"),
            "SOL/USDT": Decimal("-9"),
            "XRP/USDT": Decimal("0.1"),
        }

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat,
    )
    split_brain = SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    # 1회차 — peak(10000) 기록, 손실률 0% -> NORMAL(아직 LIQUIDATE 조건 미충족).
    await run_one_cycle(
        pool,
        service,
        split_brain,
        check_exchange=check_exchange,
        check_db=check_db,
        exchange_health_cache=exchange_health_cache,
        kill_switch=kill_switch,
        last_action=last_action,
        get_basket_returns=get_basket_returns,
    )
    assert await _latest_liquidation_request(pool, since=test_start) is None

    # 2회차 — equity 급락(90% 손실) + market-wide 급변 basket -> LIQUIDATE 발동.
    await run_one_cycle(
        pool,
        service,
        split_brain,
        check_exchange=check_exchange,
        check_db=check_db,
        exchange_health_cache=exchange_health_cache,
        kill_switch=kill_switch,
        last_action=last_action,
        get_basket_returns=get_basket_returns,
    )

    request_row = await _latest_liquidation_request(pool, since=test_start)
    assert request_row is not None, (
        "market_wide_correlated 급락에도 liquidation_request가 생기지 않음 — "
        "RTF-03 basket 배선 회귀"
    )
    assert request_row["state"] == "REQUESTED"

    # 실행 루프(리퀴데이션 워커)가 이 요청을 실제로 소비하는지 확인 — watchdog가
    # INSERT만 하고 아무도 소비하지 않는 상태가 아님을 증명한다.
    adapters = {"bitget": FakeExchangeAdapter()}
    await run_liquidation_worker_once(pool, adapters, now=request_row["requested_at"])

    consumed_row = await _latest_liquidation_request(pool, since=test_start)
    assert consumed_row["id"] == request_row["id"]
    assert consumed_row["state"] == "PLANNED", (
        "run_liquidation_worker_once가 watchdog의 REQUESTED 행을 소비하지 않음"
    )
    assert consumed_row["seed_ref"] is not None

    async with pool.acquire() as conn:
        slice_states = [
            r["state"]
            for r in await conn.fetch(
                "SELECT state FROM liquidation_slice WHERE request_id = $1 ORDER BY seq",
                request_row["id"],
            )
        ]
    assert len(slice_states) >= 3
    assert all(s == "PENDING" for s in slice_states)


async def test_isolated_loss_without_market_wide_move_does_not_liquidate(
    pool, kill_switch, tmp_path
):
    """대조군(negative) — 같은 90% 손실이라도 basket이 고립 하락(과반 미만)이면
    LIQUIDATE가 아니라 HALT(조작 의심)만 나가야 하고 liquidation_request는
    생기지 않아야 한다."""
    test_start = datetime.now(timezone.utc)
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    equity_readings = iter([Decimal("10000"), Decimal("1000")])

    async def compute_equity() -> Decimal:
        return next(equity_readings)

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    async def get_basket_returns() -> dict[str, Decimal]:
        # 4개 중 1개만 하락 — 과반 아님(고립 손실 = 조작 의심).
        return {
            "BTC/USDT": Decimal("-9"),
            "ETH/USDT": Decimal("0.1"),
            "SOL/USDT": Decimal("0.2"),
            "XRP/USDT": Decimal("0.1"),
        }

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat,
    )
    split_brain = SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    for _ in range(2):
        await run_one_cycle(
            pool,
            service,
            split_brain,
            check_exchange=check_exchange,
            check_db=check_db,
            exchange_health_cache=exchange_health_cache,
            kill_switch=kill_switch,
            last_action=last_action,
            get_basket_returns=get_basket_returns,
        )

    assert await _latest_liquidation_request(pool, since=test_start) is None
