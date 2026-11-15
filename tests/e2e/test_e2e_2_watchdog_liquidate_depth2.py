"""E2E-2 워치독 LIQUIDATE 깊이 하한 보강 (depth2) -- task-25299.

본선/deepen/depth1이 다루지 않은 입력 축의 negative 3종, 실패 주입, 게이트 적색 재현
(양성 대조 + 가상 지연 10x), PerfBudget.best_of_async 성능 단언을 추가한다.
기존 파일은 수정하지 않는다. 공용 픽스처/헬퍼는 `_watchdog_liquidate_shared.py`."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.watchdog_process import WATCHDOG_SYSTEM_ACTOR_ID
from tests.e2e._watchdog_liquidate_shared import (  # noqa: F401 -- 픽스처 재수출
    _cleanup_watchdog_state,
    _clear_stale_liquidation_requests,
    _insert_open_position,
    _run_liquidate_cycle,
    kill_switch,
)
from tests.integration.conftest import create_test_user
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import _add_virtual, reset_virtual_totals


async def _healthy() -> bool:
    return True


async def _seed(pool: asyncpg.Pool, tag: str) -> None:
    user_id = await create_test_user(pool)
    await _insert_open_position(pool, user_id, f"E2ED2{tag}{uuid4().hex[:8]}/USDT")


async def _flat_basket() -> dict[str, Decimal]:
    return {"BTC/USDT": Decimal("0.1"), "ETH/USDT": Decimal("0.2"), "SOL/USDT": Decimal("0.1")}


async def _empty_basket() -> dict[str, Decimal]:
    return {}


async def _rising_basket() -> dict[str, Decimal]:
    return {"BTC/USDT": Decimal("5"), "ETH/USDT": Decimal("6"), "SOL/USDT": Decimal("4")}


async def _cycle(pool, kill_switch, tmp_path, **kw):  # noqa: F811
    return await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=_healthy,
        check_db=_healthy,
        **kw,
    )


# ---- negative 3종 (depth1과 겹치지 않는 입력 축) ----


async def test_negative_flat_equity_with_market_wide_basket_does_not_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- equity가 peak와 같고(손실 0%) basket만 급락이면 LIQUIDATE 아님."""
    await _seed(pool, "A")
    row = await _cycle(pool, kill_switch, tmp_path, equity_readings=[Decimal("10000")] * 2)
    assert row is None, "손실 0%인데 liquidation_request 생성 -- 손실률 게이트 회귀"


async def test_negative_empty_basket_is_undecidable_and_does_not_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- 90% 손실이어도 basket이 비어 있으면(0 symbols) undecidable -> HALT."""
    await _seed(pool, "B")
    row = await _cycle(pool, kill_switch, tmp_path, get_basket_returns=_empty_basket)
    assert row is None, "빈 basket(undecidable)인데 liquidation_request 생성"


async def test_negative_rising_basket_means_isolated_loss_not_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """negative -- 90% 손실이지만 시장 basket은 상승 -> 계정 고유 손실, LIQUIDATE 아님."""
    await _seed(pool, "C")
    row = await _cycle(pool, kill_switch, tmp_path, get_basket_returns=_rising_basket)
    assert row is None, "시장이 상승인데 liquidation_request 생성 -- 상관 게이트 회귀"


# ---- 실패 주입 ----


async def test_failure_injection_basket_fetch_error_never_creates_liquidation(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """실패 주입 -- basket 조회가 예외를 던지면 fail-closed: 사이클이 예외를 전파하고
    liquidation_request/ACTIVE safety_control이 생기지 않는다(성공 위장 금지)."""
    await _seed(pool, "D")

    async def boom() -> dict[str, Decimal]:
        raise ConnectionError("injected basket fetch failure")

    with pytest.raises(ConnectionError, match="injected"):
        await _cycle(pool, kill_switch, tmp_path, get_basket_returns=boom)
    async with pool.acquire() as conn:
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM safety_control WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    assert active == 0, "basket 조회 실패 후에도 safety_control이 활성화됨(fail-open)"


# ---- 게이트 적색 재현: 입력을 망가뜨리면 단언이 실제로 잡는지 ----


async def test_gate_red_positive_control_default_inputs_do_liquidate(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """적색 재현 -- negative 3종의 입력을 정상(90% 손실 + 시장 급락 basket)으로 되돌리면
    request가 생성돼야 한다. 이 양성 대조가 깨지면 위 `is None` 단언은 공허하다."""
    await _seed(pool, "E")
    row = await _cycle(pool, kill_switch, tmp_path)
    assert row is not None, "양성 대조 실패 -- negative 단언이 아무것도 검증하지 못함"
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )


# ---- 성능 단언 (best_of, 신설 예산) ----
# 실측(로컬 2026-11-16, NORMAL 경로 run_one_cycle 1회분, best_of_async n=5):
# cpu≈0ms(15.6ms 양자화 미만), wall≈1.7ms.
# 신설 예산 BUDGET_MS=1000ms는 wall 실측의 500배 이상 여유(30배 기준 충족), 기존 예산 변경 없음.
BUDGET_MS = 1000.0


@pytest.mark.perf
async def test_perf_normal_path_cycle_best_of_within_budget_and_red_with_10x_delay(
    pool: asyncpg.Pool,
    kill_switch,  # noqa: F811
    tmp_path,
) -> None:
    """성능 단언 + 적색 재현 -- 손실 없는 2사이클(NORMAL)을 best_of_async로 측정해 예산
    이내임을 단언하고, 같은 측정에 예산의 10배 가상 지연을 주입하면 예산을 초과(적색)함을
    확인한다(단언이 실제로 지연을 잡는지)."""
    perf = PerfBudget()
    reset_virtual_totals()
    injected_ms = 0.0

    async def one() -> object:
        await _cycle(pool, kill_switch, tmp_path, equity_readings=[Decimal("10000")] * 2)
        if injected_ms:
            _add_virtual(injected_ms / 1000, injected_ms / 1000)
        return None

    sample = await perf.best_of_async(one, n=5, warmup=1)
    print(
        f"\ndepth2 normal-path cycle best_of cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )
    assert sample.cpu_ms < BUDGET_MS, perf.describe(sample, budget_ms=BUDGET_MS)

    injected_ms = BUDGET_MS * 10
    red = await perf.best_of_async(one, n=3, warmup=0)
    reset_virtual_totals()
    assert red.cpu_ms >= BUDGET_MS, "10x 가상 지연을 주입했는데 예산 단언이 적색이 되지 않음"
