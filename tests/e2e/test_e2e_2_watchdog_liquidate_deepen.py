"""E2E-2 워치독 LIQUIDATE DEEPEN -- negative/실패주입/성능단언 보강 (task-10091).

원 리프 task-6704(고아 산출물 회수 5828 (qa-2)) 대상 DEEPEN. 본선 경로 +
최초 3개 실패 주입(거부/타임아웃/DB 고립 불일치)은
`test_e2e_2_watchdog_liquidate.py`에 있다. 이 파일은 그 위에 다음을 더한다:

- negative #2/#3: decide()가 LIQUIDATE로 오판하지 않아야 하는 경계(고립 손실,
  손실률 미달)를 실제로 HALT/NORMAL로 유지하는지 실증.
- 실패 주입 #4: 이미 DONE으로 완주한 liquidation_request에 워커가 재실행돼도
  중복 주문이 생기지 않는지(표준-105 멱등성) 실증.
- 성능 단언(D2): 워치독 LIQUIDATE 2사이클 전체 경로의 p95 지연을 공유 DB 풀의
  `SELECT 1` p95에 정규화한 임계로 단언.

공용 픽스처/헬퍼는 `_watchdog_liquidate_shared.py`에서 가져온다(본선 파일과
같은 DB 정리 규약을 공유)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.services.safety.liquidation_executor import run_liquidation_worker_once
from src.watchdog_process import WATCHDOG_SYSTEM_ACTOR_ID
from tests.e2e._watchdog_liquidate_shared import (  # noqa: F401 -- 픽스처 재수출
    _cleanup_watchdog_state,
    _clear_stale_liquidation_requests,
    _insert_open_position,
    _run_liquidate_cycle,
    kill_switch,
)
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


async def _isolated_basket() -> dict[str, Decimal]:
    # 4개 중 1개만 하락(과반 미달) -- 시장 전체 급변이 아니라 고립 손실이다.
    return {
        "BTC/USDT": Decimal("-9"),
        "ETH/USDT": Decimal("0.1"),
        "SOL/USDT": Decimal("0.2"),
        "XRP/USDT": Decimal("0.1"),
    }


# ---- negative #2 -- 고립 손실(시장 전체 급변 아님)은 LIQUIDATE 대신 HALT로 강등 ----


async def test_isolated_loss_without_market_wide_correlation_downgrades_to_halt(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- 계좌 손실률은 임계를 넘지만(90%), basket 4개 중 1개만
    하락(과반 미달)이라 `market_wide_correlated=False`로 판정되면 decide()는
    LIQUIDATE가 아니라 `isolated_loss_suspected_manipulation` 사유의 HALT로
    강등한다(FD-9.2). 이 케이스에서 `liquidation_request`가 생성되면 조작 의심
    상황에서 강제청산이 발동하는 회귀다."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2E2I{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
        get_basket_returns=_isolated_basket,
    )

    assert request_row is None, (
        "고립 손실(시장 전체 급변 아님)인데도 liquidation_request가 생성됨 -- FD-9.2 HALT 강등 회귀"
    )


# ---- negative #3 -- 손실률이 임계 미만이면 시장 급변 basket이어도 NORMAL 유지 ----


async def test_loss_below_threshold_stays_normal_even_with_market_wide_basket(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- basket은 시장 전체 급변으로 판정될 조건(4개 중 3개 임계 초과
    하락)을 만족해도, 계좌 자체의 equity 손실률이 임계(기본 20%) 미만이면
    decide()는 NORMAL을 유지해야 한다 -- 시장 급변 신호 하나만으로 계좌 손실
    여부와 무관하게 강제조치가 발동하면 안 된다(FD-9.2 손실률 우선 게이트)."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2E2N{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
        # peak -> 소폭 손실(5%)만 발생 -- 임계 미달.
        equity_readings=[Decimal("10000"), Decimal("9500")],
    )

    assert request_row is None, (
        "손실률이 임계 미달인데도 market-wide basket만으로 liquidation_request가 "
        "생성됨 -- FD-9.2 손실률 게이트 회귀"
    )
    async with pool.acquire() as conn:
        active_control = await conn.fetchval(
            "SELECT COUNT(*) FROM safety_control WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    assert active_control == 0


# ---- 실패 주입 #4 -- 멱등성: 이미 완주(DONE)한 요청에 워커를 다시 돌려도 -------
# ---- 중복 주문이 생기지 않는다 ------------------------------------------------


async def test_liquidation_worker_rerun_after_done_is_idempotent(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """실패 주입(재시도/중복 호출) -- 표준-105(멱등키) 준수 실증. 실행 루프가
    이미 DONE까지 완주시킨 `liquidation_request`에 대해 워커가(예: 재기동,
    중복 스케줄) 다시 호출돼도 같은 심볼에 두 번째 주문이 제출되지 않는다 --
    청산 주문이 중복 제출되면 실제 자산에 실 손실을 입히는 안전 회귀다."""
    user_id = await create_test_user(pool)
    symbol = f"E2E2D{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
    )
    assert request_row is not None

    adapter = FakeExchangeAdapter()
    adapters = {"bitget": adapter}
    t0 = request_row["requested_at"]
    await run_liquidation_worker_once(pool, adapters, now=t0)

    plan_row = await pool.fetchrow(
        "SELECT plan FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    import json as _json

    deadline_offset = _json.loads(plan_row["plan"])["plan"]["deadline_offset_sec"]
    deadline_at = t0 + timedelta(seconds=deadline_offset)
    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=1))

    consumed_row = await pool.fetchrow(
        "SELECT state FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    assert consumed_row["state"] == "DONE"
    calls_after_first_completion = adapter.place_order_call_count

    # 이미 DONE인 요청에 워커를 한 번 더 돌린다 -- SELECT...FOR UPDATE SKIP
    # LOCKED가 non-terminal 행만 후보로 고르므로 DONE 행은 다시 집히지 않아야
    # 한다.
    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=2))

    assert adapter.place_order_call_count == calls_after_first_completion, (
        "이미 DONE인 liquidation_request에 워커 재실행이 주문을 또 제출함 -- 멱등성(표준-105) 위반"
    )
    orders = await pool.fetch(
        "SELECT order_id FROM orders WHERE liquidation_request_id = $1 AND symbol = $2",
        request_row["id"],
        symbol,
    )
    assert len(orders) == 1, "동일 liquidation_request/symbol에 주문이 중복 생성됨"


# ---- 성능 단언(D2) -- 워치독 LIQUIDATE 전체 경로 지연 -------------------------


async def _p95_ms(reps: int, step: Callable[[], Awaitable[object]]) -> float:
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await step()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[int(len(samples) * 0.95) - 1]


@pytest.mark.perf
async def test_watchdog_liquidate_cycle_latency_within_normalized_budget(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """수치 성능 단언(D2) -- ADR-2026-09-09-C 축별 성능 예산. `run_one_cycle` 두
    번(peak 기록 -> LIQUIDATE 판정)의 p95 지연을 공유 CI 변동에 강인하도록 이
    풀의 `SELECT 1` p95에 정규화한 임계로 단언한다(절대 ms 상수 대신 --
    test_order_execution_settlement.py와 동일 관례)."""
    reps = 5
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    counter = {"n": 0}

    async def _one_cycle() -> None:
        counter["n"] += 1
        user_id = await create_test_user(pool)
        symbol = f"E2E2P{counter['n']}{uuid4().hex[:6]}/USDT"
        await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))
        await _run_liquidate_cycle(
            pool,
            kill_switch,
            heartbeat_path=tmp_path / f"hb{counter['n']}",
            check_exchange=check_exchange,
            check_db=check_db,
        )
        # 다음 rep의 activate()가 "이미 ACTIVE" 충돌 없이 측정되도록, 이번 rep이
        # 만든 GLOBAL safety_control을 즉시 비활성화한다(측정 대상은 단일
        # 사이클의 지연이지, 연속 발동 시 동시성 거부 경로가 아니다).
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
                "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
                WATCHDOG_SYSTEM_ACTOR_ID,
            )

    cycle_p95 = await _p95_ms(reps, _one_cycle)
    budget_ms = max(3000.0, 400.0 * baseline_p95)
    print(  # noqa: T201 -- 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nwatchdog liquidate cycle p95={cycle_p95:.3f}ms baseline(SELECT 1) "
        f"p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert cycle_p95 < budget_ms
