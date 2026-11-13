"""R-3 `risk_inputs_assembler.py` — 'filled or None, never defaulted' negative evidence.

Split out of `test_risk_inputs_assembler.py` (task-24189, 500-line ratchet,
CLAUDE.md §7). Test-only; product code is untouched.
"""

from __future__ import annotations

import uuid
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
    insert_position,
    intent,
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def _assemble(pool, symbol, strategy_id, balances):
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    inputs = await assemble_risk_inputs(
        pool,
        caches(),
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, strategy_id),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )
    return user_id, inputs


async def test_non_usdt_only_balance_equity_none_not_zero(pool):
    """negative 1 — a balance list without USDT must not be summed into a 0 equity."""
    symbol = f"NEG1-{uuid.uuid4().hex[:8]}/USDT"
    balances = [
        AccountBalance(exchange="bitget", asset="BTC", total=Decimal("2"), available=Decimal("2"))
    ]
    _, inputs = await _assemble(pool, symbol, "strat-neg1", balances)
    assert inputs.equity.total_equity != Decimal("0")
    assert inputs.equity.total_equity is None


async def test_open_position_is_reflected_not_defaulted_to_zero(pool):
    """negative 2 — a real position must surface as count 1 / its quantity,
    so the zero asserted in the empty case is not a hard-coded default."""
    symbol = f"NEG2-{uuid.uuid4().hex[:8]}/USDT"
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    await insert_position(
        pool,
        user_id=user_id,
        symbol=symbol,
        exchange="bitget",
        strategy_id="strat-neg2",
        quantity=Decimal("3"),
        average_entry_price=Decimal("100"),
    )
    inputs = await assemble_risk_inputs(
        pool,
        caches(),
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-neg2"),
        balances=[
            AccountBalance(
                exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
            )
        ],
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )
    assert inputs.exposure.open_positions_count == 1
    assert inputs.exposure.position_quantity == Decimal("3")


async def test_unobservable_safety_fields_never_become_false_or_empty(pool):
    """negative 3 — unobservable safety fields stay None; falsy defaults
    (False / () / 0) would silently pass fail-closed rules."""
    symbol = f"NEG3-{uuid.uuid4().hex[:8]}/USDT"
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]
    _, inputs = await _assemble(pool, symbol, "strat-neg3", balances)
    s = inputs.safety
    for name in (
        "active_control_scopes",
        "distrust_sources_available",
        "connection_fresh",
        "rule_bundle_active",
        "data_distrust_level",
    ):
        value = getattr(s, name)
        assert value is None, (name, value)
        assert value != False  # noqa: E712 - guards bool/0 coercion of None
    assert inputs.equity.account_daily_pnl_pct is None
    assert inputs.equity.account_drawdown_pct is None


async def test_missing_distrust_level_denies_even_for_reduce_only(pool):
    """negative 4 — an unobserved distrust level (None) must DENY with the
    missing-input reason even for a reduce-only intent; reduce_only may only
    waive a known distrust level, never an unknown one."""
    symbol = f"NEG4-{uuid.uuid4().hex[:8]}/USDT"
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]
    _, inputs = await _assemble(pool, symbol, "strat-neg4", balances)
    assert inputs.safety.data_distrust_level is None
    isolated = inputs.model_copy(
        update={
            "safety": inputs.safety.model_copy(update={"active_control_scopes": ()}),
            "intent": inputs.intent.model_copy(update={"reduce_only": True}),
        }
    )
    result = safety_state.safety_state(isolated, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_INPUT_MISSING:safety.data_distrust_level"
