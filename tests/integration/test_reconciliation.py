"""9.6 통합테스트 — 실제 dev DB 대상."""
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerService
from src.core.safety.reconciliation import ReconciliationService


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def circuit_breaker(pool):
    return CircuitBreakerService(pool, load_risk_policy().circuit_breaker)


@pytest.fixture
def reconciliation(pool, circuit_breaker):
    return ReconciliationService(pool, circuit_breaker)


async def _record(reconciliation, *, symbol=None, exchange="bitget", discrepancy_pct=None):
    symbol = symbol or f"TEST{uuid4().hex[:8]}/USDT"
    return await reconciliation.record_and_escalate(
        user_id=uuid4(),
        symbol=symbol,
        exchange=exchange,
        internal_value={"qty": "1.0"},
        external_value={"qty": "1.1"},
        discrepancy_pct=discrepancy_pct,
    )


async def test_single_discrepancy_only_triggers_auto_recovery(reconciliation, circuit_breaker):
    outcome = await _record(reconciliation)
    assert outcome.action == "auto_recovery"
    state = await circuit_breaker.get_state()
    assert state.level == CircuitBreakerLevel.NORMAL


async def test_three_within_hour_escalates_to_restricted(reconciliation, circuit_breaker):
    symbol = f"TEST{uuid4().hex[:8]}/USDT"
    for _ in range(3):
        outcome = await _record(reconciliation, symbol=symbol)

    assert outcome.action == "circuit_breaker_restricted"
    state = await circuit_breaker.get_state()
    assert state.level == CircuitBreakerLevel.RESTRICTED


async def test_five_within_day_escalates_to_halted(reconciliation, circuit_breaker):
    symbol = f"TEST{uuid4().hex[:8]}/USDT"
    for _ in range(5):
        outcome = await _record(reconciliation, symbol=symbol)

    assert outcome.action == "full_halt_rca_required"
    state = await circuit_breaker.get_state()
    assert state.level == CircuitBreakerLevel.HALTED


async def test_single_severe_discrepancy_immediately_halts(reconciliation, circuit_breaker):
    symbol = f"TEST{uuid4().hex[:8]}/USDT"
    outcome = await _record(reconciliation, symbol=symbol, discrepancy_pct=Decimal("15"))

    assert outcome.action == "full_halt_rca_required"
    state = await circuit_breaker.get_state()
    assert state.level == CircuitBreakerLevel.HALTED


async def test_different_symbols_counted_independently(reconciliation):
    symbol_a = f"TESTA{uuid4().hex[:8]}/USDT"
    symbol_b = f"TESTB{uuid4().hex[:8]}/USDT"
    await _record(reconciliation, symbol=symbol_a)
    await _record(reconciliation, symbol=symbol_a)
    outcome_b = await _record(reconciliation, symbol=symbol_b)

    assert outcome_b.count_1h == 1
    assert outcome_b.action == "auto_recovery"


async def test_repeated_severe_events_do_not_escalate_past_halted(reconciliation, circuit_breaker):
    symbol = f"TEST{uuid4().hex[:8]}/USDT"
    await _record(reconciliation, symbol=symbol, discrepancy_pct=Decimal("20"))
    await _record(reconciliation, symbol=symbol, discrepancy_pct=Decimal("25"))

    state = await circuit_breaker.get_state()
    assert state.level == CircuitBreakerLevel.HALTED  # emergency로 넘어가지 않음
