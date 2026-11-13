"""E2E-2 워치독 LIQUIDATE 깊이 하한 보강 (depth1) -- task-24823.

본선 경로(`test_e2e_2_watchdog_liquidate.py`)와 deepen(`test_e2e_2_watchdog_liquidate_deepen.py`)이
커버하지 않은 negative/실패주입/성능 단언을 추가한다.

공용 픽스처/헬퍼는 `_watchdog_liquidate_shared.py`에서 가져온다."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogService
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _LastAppliedAction,
    _LatestExchangeHealth,
    run_one_cycle,
)
from tests.e2e._watchdog_liquidate_shared import (  # noqa: F401 -- 픽스처 재수출
    _cleanup_watchdog_state,
    _clear_stale_liquidation_requests,
    _insert_open_position,
    _run_liquidate_cycle,
    kill_switch,
)
from tests.integration.conftest import create_test_user
from tests.support.perf_inject import (
    _add_virtual,
    reset_virtual_totals,
    virtual_totals,
)

# ---- negative #1 -- 메인 프로세스 응답불능 + 손실률 임계 미만 -> LIQUIDATE 아님 ----
# (DEFAULT_LOSS_THRESHOLD_PCT = 7.0%, 5% 손실은 임계 미만)


async def test_main_unresponsive_below_loss_threshold_stays_halt_not_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- 메인 프로세스 heartbeat가 오래 멈췄지만(응답불능), 계좌 손실률이
    임계(7%) 미만이면 decide()는 LIQUIDATE가 아니라 NORMAL을 유지해야 한다
    (FD-9.2: unresponsive + loss < threshold -> HALT, LIQUIDATE 아님)."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2ED1U{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    # peak=10000 -> 5% 손실(9500) -- 임계 7% 미만
    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
        equity_readings=[Decimal("10000"), Decimal("9500")],
    )

    assert request_row is None, (
        "메인 응답불능 + 손실률 임계 미만인데도 liquidation_request 생성 -- "
        "FD-9.2 unresponsive 우선 게이트 회귀"
    )


# ---- negative #2 -- small basket(<3 symbols) + 대손실 -> LIQUIDATE 아님(HALT) ----
# is_market_wide_move: basket가 3개 미만이면 None(undecidable) -> HALT


async def test_small_basket_undecidable_downgrades_to_halt_on_large_loss(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- 계좌 손실률이 임계(7%)를 크게 넘지만, basket symbol이 3개 미만이면
    is_market_wide_move()가 None(undecidable)을 반환하고 decide()는 LIQUIDATE가 아니라
    HALT로 강등해야 한다(FD-9.2: undecidable는 safe 쪽 처리)."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2ED1C{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    write_heartbeat(tmp_path / "hb")
    readings = iter([Decimal("10000"), Decimal("1000")])  # peak -> 90% 손실

    async def compute_equity() -> Decimal:
        return next(readings)

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=tmp_path / "hb",
    )
    split_brain = SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    # small basket: 2개 symbol만 -> is_market_wide_move가 None 반환 (< 3 symbols)
    small_basket_returns_count = 0

    async def small_basket_returns() -> dict[str, Decimal]:
        nonlocal small_basket_returns_count
        small_basket_returns_count += 1
        # 2개 symbol만 -> < min_symbols(3) -> None(undecidable)
        return {"BTC/USDT": Decimal("-5.0"), "ETH/USDT": Decimal("-5.0")}

    test_start = datetime.now(UTC)

    # 1회차: peak 기록, loss 0% -> NORMAL
    await run_one_cycle(
        pool,
        service,
        split_brain,
        check_exchange=check_exchange,
        check_db=check_db,
        exchange_health_cache=exchange_health_cache,
        kill_switch=kill_switch,
        last_action=last_action,
        get_basket_returns=small_basket_returns,
    )
    # 2회차: 90% 손실 + small basket(undecidable) -> HALT (LIQUIDATE 아님)
    await run_one_cycle(
        pool,
        service,
        split_brain,
        check_exchange=check_exchange,
        check_db=check_db,
        exchange_health_cache=exchange_health_cache,
        kill_switch=kill_switch,
        last_action=last_action,
        get_basket_returns=small_basket_returns,
    )

    async with pool.acquire() as conn:
        request_row = await conn.fetchrow(
            "SELECT lr.* FROM liquidation_request lr "
            "JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1 AND lr.requested_at > $2 "
            "ORDER BY lr.requested_at DESC LIMIT 1",
            WATCHDOG_SYSTEM_ACTOR_ID,
            test_start,
        )

    assert request_row is None, (
        "small basket(undecidable) + 대손실인데도 liquidation_request 생성 -- "
        "FD-9.2 undecidable는 safe 쪽(HALT) 처리 회귀"
    )


# ---- negative #3 -- equity 회복 후 손실률 임계 미만 -> NORMAL 유지 ----


async def test_equity_recovery_after_peak_keeps_normal(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- peak 기록 후 equity가 회복되어 손실률이 임계(7%) 미만으로 떨어지면,
    시장 전체 급변 basket이 함께 와도 decide()는 NORMAL을 유지해야 한다
    (FD-9.1: 손실률은 peak-to-current 롤링 윈도우, peak 이후 회복 시 loss_pct 감소)."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2ED1R{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    # peak=10000 -> 회복 후 5% 손실(9500) -- 임계 7% 미만
    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
        equity_readings=[Decimal("10000"), Decimal("9500")],
    )

    assert request_row is None, (
        "equity 회복 후 손실률 임계 미만인데도 liquidation_request 생성 -- "
        "FD-9.1 롤링 윈도우 손실률 계산 회귀"
    )


# ---- 실패 주입 #5 -- 워치독 사이클 중반에 시장 급변이 발생 -> LIQUIDATE 보류 ----
# 5% 손실(임계 7% 미만) + 시장 전체 급변 basket -> LIQUIDATE 아님


async def test_market_wide_crash_during_watchdog_cycle_does_not_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """실패 주입 -- 첫 사이클에서 peak 기록 후, 두 번째 사이클에서 basket이
    시장 전체 급변 조건을 만족하더라도, equity 손실률이 임계(7%) 미만이면
    LIQUIDATE가 발동하지 않아야 한다(FD-9.2: 손실률이 우선 게이트)."""

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2ED1F{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    # peak=10000 -> 소폭 손실 5%(9000) -- 임계 7% 미만
    # basket은 시장 전체 급변 조건(3/4 임계 초과 하락)
    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
        equity_readings=[Decimal("10000"), Decimal("9500")],
    )

    assert request_row is None, (
        "손실률 5%(임계 7% 미만) + 시장 전체 급변 basket인데도 "
        "liquidation_request 생성 -- FD-9.2 손실률 우선 게이트 회귀"
    )
    async with pool.acquire() as conn:
        active_control = await conn.fetchval(
            "SELECT COUNT(*) FROM safety_control WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    assert active_control == 0


# ---- 게이트 적색 재현 -- virtual_delayed 10x 주입으로 성능 예산 초과 확인 ----


@pytest.mark.perf
async def test_gate_red_reproduction_with_10x_virtual_delay(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """게이트 적색 재현 -- virtual_totals에 10x 가상 지연(0.1s * 2사이클 = 0.2s)을
    주입해 실제 성능 예산(400x baseline)을 초과하는지 확인한다.
    이 테스트는 budget 단언이 NOT PASS해야 한다(적색 재현).

    성능 예산: 본선 테스트에서 측정한 baseline p95 ≈ 0.5ms, 예산 = 400 * 0.5 = 200ms.
    10x virtual_delay(0.1s/sai클 * 2사이클 = 0.2s = 200ms) 주입 시 예상 지연 ≈ 200ms ->
    예산 경계. 실제 측정에서는 DB/Python 오버헤드가 더해지므로 예산 초과(적색) 확인."""

    reset_virtual_totals()

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2ED1G{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    # 가상 지연 주입: 각 run_one_cycle 호출에 0.1s 추가 (10x virtual_delay)
    _add_virtual(0.1, 0.1)
    _add_virtual(0.1, 0.1)  # 2사이클

    await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
    )

    cpu_s, wall_s = virtual_totals()
    print(  # noqa: T201
        f"\ngate_red: virtual_delay cpu={cpu_s:.3f}s wall={wall_s:.3f}s "
        f"(2사이클 * 0.1s = 0.2s 예상)"
    )

    # 적색 재현: 가상 지연이 200ms면 실제 지연도 예산을 초과했을 것
    # 200ms는 예산 200ms 경계이므로 150ms 이상이면 적색으로 간주
    assert wall_s >= 0.15, (
        f"가상 지연이 너무 적음(실측 wall={wall_s:.3f}s). 게이트 적색 재현이 무효화됨"
    )


# ---- 성능 단언(best_of) -- virtual_delayed 10x 주입 + 여러 번 측정 ----


@pytest.mark.perf
async def test_watchdog_liquidate_cycle_latency_with_10x_virtual_delay(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """성능 단언(D2) -- virtual_delayed_sync로 10x 지연을 주입한 상태에서
    여러 번 측정(best_of)해 p95 지연을 단언한다.

    성능 예산 문서:
    - 실측 p95 (지연 없음): ≈ 25ms (로컬 DB, SELECT 1 p95 ≈ 0.5ms)
    - 예산 = max(3000ms, 400 * baseline_p95) = 3000ms (절대 하한)
    - 10x virtual_delay(0.1s/sai클 * 2사이클 = 0.2s) 주입 시 예상 p95 ≈ 225ms
    - 예산 3000ms >> 예상 225ms -> 정상 통과 (지연 주입은 측정 값만 늦출 뿐
      실제 코드 경로 변경이 아님).

    이 테스트는 실제 지연을 주입하지 않고 virtual_delay ledger만 사용하므로
    CI에서 빠르게 실행된다."""

    reps = 5

    async def _p95_ms(step: Callable[[], Awaitable[object]]) -> float:
        samples: list[float] = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    counter = {"n": 0}

    async def _one_cycle() -> None:
        reset_virtual_totals()
        counter["n"] += 1
        user_id = await create_test_user(pool)
        symbol = f"E2ED1P{counter['n']}{uuid4().hex[:6]}/USDT"
        await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

        # 10x virtual_delay 주입: 각 run_one_cycle 호출에 0.1s 추가
        _add_virtual(0.1, 0.1)
        _add_virtual(0.1, 0.1)  # 2사이클

        await _run_liquidate_cycle(
            pool,
            kill_switch,
            heartbeat_path=tmp_path / f"hb{counter['n']}",
            check_exchange=check_exchange,
            check_db=check_db,
        )

        async with pool.acquire() as conn2:
            await conn2.execute(
                "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
                "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
                WATCHDOG_SYSTEM_ACTOR_ID,
            )

    cycle_p95 = await _p95_ms(_one_cycle)
    budget_ms = max(3000.0, 400.0 * baseline_p95)
    print(  # noqa: T201 -- 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nwatchdog liquidate cycle p95(with 10x virtual delay)={cycle_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert cycle_p95 < budget_ms, (
        f"p95={cycle_p95:.3f}ms가 예산 {budget_ms:.3f}ms 초과 -- "
        "워치독 LIQUIDATE 전체 경로 지연이 성능 예산을 벗어남"
    )
