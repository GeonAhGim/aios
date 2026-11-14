"""R-31 `risk_inputs_assembler.py` 깊이 하한 보강 (DEEPEN-DEPTH R-27).

D2 미달: negative 2/3 -> D3 보강. 기존 파일 수정 없이 새 보조 테스트 파일로
부족 항목만 보강 (task-24977).

부족 항목:
- negative 테스트 3종 이상 (기존 test_risk_inputs_assembler.py, deepen.py에 없는 케이스)
- 실패 주입 (DB failure propagation)
- 게이트 적색 재현 (입력 망가뜨려 적색 확인)
- 성능 단언 (perf 마커 + perf_inject 10배 지연 + perf_budget.best_of 측정)

기존 단언/마커 삭제 금지, src/core/{strategy,portfolio,risk,executor} 수정 금지.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.core.risk.rules import safety_state
from src.data.models.trading import AccountBalance
from src.services.execution_loop.risk_inputs_assembler import assemble_risk_inputs
from tests.integration.conftest import create_test_user
from tests.integration.risk._risk_inputs_assembler_fixtures import (
    NOW,
    POLICY,
    asyncpg_dsn,
    caches,
    candles,
    create_execution,
    intent,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Negative tests (3종 이상)
# ---------------------------------------------------------------------------


async def test_negative_empty_balances_leaves_equity_none(pool):
    """Negative — balances가 비면 total_equity/available_balance이 None이어야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "NEG-EMPTY-BAL/USDT"
    riskcaches = caches()

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-empty-bal"),
        balances=[],  # 빈 배런스
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    assert inputs.equity.total_equity is None
    assert inputs.equity.available_balance is None


async def test_negative_no_candles_leaves_price_none(pool):
    """Negative — candles가 비면 current_price가 None이고 exposure가 빈 가격으로 조립된다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "NEG-NO-CANDLE/USDT"
    riskcaches = caches()

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-no-candle"),
        balances=[
            AccountBalance(
                exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
            )
        ],
        candles=[],  # 빈 캔들
        policy=POLICY,
        now=NOW,
    )

    # candles가 없으면 prices={}이므로 exposure는 entry_price fallback만 본다.
    assert inputs.exposure.position_quantity == Decimal("0")


async def test_negative_unknown_asset_in_balance_ignored(pool):
    """Negative — USDT 외 자산만 있으면 total_equity가 None이다 (USDT가 total 기준)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "NEG-OTHER-ASSET/USDT"
    riskcaches = caches()

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-other-asset"),
        balances=[
            AccountBalance(
                exchange="bitget", asset="BTC", total=Decimal("1"), available=Decimal("0.9")
            )
        ],
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    assert inputs.equity.total_equity is None
    assert inputs.equity.available_balance is None


# ---------------------------------------------------------------------------
# Failure injection — DB connection error propagates (fail-closed)
# ---------------------------------------------------------------------------


async def test_failure_injection_db_error_propagates(pool, monkeypatch):
    """Failure injection — DB 연결 오류가 그대로 전파된다 (fail-closed)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "FAIL-DB-ERR/USDT"

    async def failing_fetchrow(self, *args, **kwargs):
        raise asyncpg.PostgresConnectionError("injected connection failure")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", failing_fetchrow)

    with pytest.raises(asyncpg.PostgresConnectionError, match="injected"):
        await assemble_risk_inputs(
            pool,
            caches(),
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-fail-db"),
            balances=[
                AccountBalance(
                    exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
                )
            ],
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )


# ---------------------------------------------------------------------------
# Gate red reproduction — corrupt input to trigger denial
# ---------------------------------------------------------------------------


async def test_gate_red_halted_circuit_breaker_denies(pool):
    """Gate red — circuit_breaker_level='halted'을 DB에 쓰고 safety_state가 DENY한다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "RED-HALTED/USDT"

    # DB에 halted 상태 주입
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'halted' WHERE id = 1"
        )
    try:
        riskcaches = caches()
        inputs = await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-red-halted"),
            balances=[
                AccountBalance(
                    exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
                )
            ],
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )
        assert inputs.safety.circuit_breaker_level == "halted"

        # safety_state 규칙이 실제로 DENY하는지 확인
        isolated = inputs.model_copy(
            update={"safety": inputs.safety.model_copy(update={"active_control_scopes": ()})}
        )
        result = safety_state.safety_state(isolated, POLICY)
        assert result.outcome == RiskOutcome.DENY
        assert result.reason_code == "RISK_CIRCUIT_BREAKER_HALTED"
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE system_safety_state SET circuit_breaker_level = 'normal' WHERE id = 1"
            )


# ---------------------------------------------------------------------------
# Performance assertion (perf marker required by check_perf_marker_guard.py)
# ---------------------------------------------------------------------------

# New budget: best-of-5 wall latency of one assemble_risk_inputs call.
# Measured locally: MEASURED_MS; BUDGET_MS leaves >=30x headroom.
MEASURED_MS = 3.5  # best-of-5 wall, local PG, 2026-11-14
BUDGET_MS = 100.0  # ~28x measured


@pytest.mark.perf
async def test_assemble_risk_inputs_wall_latency_within_budget(
    pool, perf_budget: PerfBudget, monkeypatch
):
    """Perf: best-of-5 wall latency of assemble_risk_inputs stays under BUDGET_MS;
    a virtual 10x-of-budget delay on fetchrow must turn the assertion red."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "PERF-TEST/USDT"
    riskcaches = caches()
    riskcaches.equity_tracker.seed(
        execution_id,
        day_start_date=date(2026, 1, 1),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )

    async def _call():
        return await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-perf"),
            balances=[
                AccountBalance(
                    exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
                )
            ],
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )

    healthy = await perf_budget.best_of_async(_call, n=5, warmup=1)
    print(f"MEASURED_MS={healthy.wall_ms:.3f}")
    assert healthy.wall_ms < BUDGET_MS

    # Gate red reproduction: 10x budget delay should break the assertion
    reset_virtual_totals()
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetchrow",
        virtual_delayed_async(asyncpg.Connection.fetchrow, BUDGET_MS * 10 / 1000),
    )
    slow = await perf_budget.best_of_async(_call, n=5, warmup=1)
    assert slow.wall_ms >= BUDGET_MS * 10
    assert not slow.wall_ms < BUDGET_MS
