"""E2E-1 D2: 음성 3종, 거래소 실패 주입, 동일 성능 단언의 적색 재현.

기존 E2E의 실제 게이트/DB/어댑터 조립을 재사용한다. 앱 코드는 변경하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderStatus
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.services.execution_loop.tick import run_execution_tick
from tests.e2e.test_e2e_1_paper_order_lifecycle import (
    _bitget_adapter,
    _engines,
    _seed_compliance_mandate,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.test_execution_tick import _create_execution
from tests.support.perf_budget import PerfBudget, PerfSample
from tests.support.perf_inject import virtual_delayed_async


async def _assert_idle_without_orders(pool, execution_id, adapter):
    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT fsm_state FROM strategy_executions WHERE id=$1", execution_id
            )
            == "IDLE"
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM orders WHERE execution_id=$1", execution_id)
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM positions WHERE execution_id=$1", execution_id
            )
            == 0
        )


async def test_negative_no_signal_does_not_submit(pool: asyncpg.Pool):
    user_id = await create_test_tenant(pool)
    await _seed_compliance_mandate(pool, user_id)
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)
    await run_execution_tick(pool, adapter, execution_id, **_engines(pool))
    await _assert_idle_without_orders(pool, execution_id, adapter)


async def test_negative_missing_execution_fails_before_exchange(pool: asyncpg.Pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM strategy_executions WHERE id=$1", execution_id)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)
    with pytest.raises(ValueError, match="존재하지 않는 실행"):
        await run_execution_tick(pool, adapter, execution_id, **_engines(pool))
    assert adapter.place_order_call_count == 0


async def test_negative_missing_compliance_mandate_denies(pool: asyncpg.Pool):
    """I-09/I-10: 정상 신호여도 컴플라이언스 위임장이 없으면 주문 금지."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)
    engines = _engines(pool)
    gate = engines["pre_submit_gate"]
    reasons = []

    async def observed_gate(context):
        decision = await gate(context)
        reasons.extend(decision.reason_codes)
        return decision

    engines["pre_submit_gate"] = observed_gate
    await run_execution_tick(pool, adapter, execution_id, **engines)
    assert "CM_MANDATE_MISSING" in reasons
    await _assert_idle_without_orders(pool, execution_id, adapter)


async def test_failure_injection_exchange_timeout_leaves_unknown(pool: asyncpg.Pool):
    """송신 결과가 불명확하면 재주문/가상 체결 없이 UNKNOWN으로 보존한다."""
    user_id = await create_test_tenant(pool)
    await _seed_compliance_mandate(pool, user_id)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    failure = ExchangeError(ExchangeErrorKind.TRANSIENT_NETWORK, venue="bitget")

    async def timeout(_order):
        raise failure

    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED, on_place_order=timeout)
    with pytest.raises(ExchangeError) as caught:
        await run_execution_tick(pool, adapter, execution_id, **_engines(pool))
    assert caught.value is failure
    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT status FROM orders WHERE execution_id=$1", execution_id)
        assert [row["status"] for row in rows] == ["UNKNOWN"]
        assert (
            await conn.fetchval(
                "SELECT fsm_state FROM strategy_executions WHERE id=$1", execution_id
            )
            == "BUY_ORDER_PENDING"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM positions WHERE execution_id=$1", execution_id
            )
            == 0
        )


def _assert_tick_budget(sample: PerfSample):
    assert sample.wall_ms < 10000.0, f"E2E-1 tick budget: {sample.wall_ms:.3f}ms"


@pytest.mark.perf
async def test_perf_lifecycle_and_gate_red_10x_delay(
    pool: asyncpg.Pool, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
):
    """매 표본은 새 실행의 신호→실제 게이트→체결→포지션 전체 경로다.

    예산 근거: 2026-11-14 로컬 best-of-3 CPU 31.250ms, wall 72.569ms.
    10000ms는 실측 왕복의 137배로 DB/CI 경합 여유를 포함한다(30배 이상).
    비동기 best_of_async는 best_of와 동일한 최소 CPU 표본 선택을 사용한다.
    10초 왕복 예산의 10배를 place_order에 가상 주입해 같은 단언이 적색인지 확인한다.
    """
    user_id = await create_test_tenant(pool)
    await _seed_compliance_mandate(pool, user_id)
    adapter = _bitget_adapter(place_order_result_status=OrderStatus.FILLED)

    async def lifecycle():
        user_id = await create_test_tenant(pool)
        await _seed_compliance_mandate(pool, user_id)
        execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
        await run_execution_tick(pool, adapter, execution_id, **_engines(pool))
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT quantity, average_entry_price FROM positions WHERE execution_id=$1",
                execution_id,
            )
            assert row is not None
            assert row["quantity"] == Decimal("20")
            assert row["average_entry_price"] == Decimal("50")
        return execution_id

    healthy = await perf_budget.best_of_async(lifecycle, n=3, warmup=1)
    print(f"E2E-1 baseline cpu={healthy.cpu_ms:.3f}ms wall={healthy.wall_ms:.3f}ms")
    _assert_tick_budget(healthy)
    assert adapter.place_order_call_count == 4
    monkeypatch.setattr(adapter, "place_order", virtual_delayed_async(adapter.place_order, 100.0))
    degraded = await perf_budget.best_of_async(lifecycle, n=3)
    assert adapter.place_order_call_count == 7
    assert degraded.wall_ms >= 100000.0
    with pytest.raises(AssertionError, match="E2E-1 tick budget"):
        _assert_tick_budget(degraded)
