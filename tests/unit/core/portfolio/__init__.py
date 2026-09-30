"""DEEPEN(task-9991, DEEPEN of task-6704): negative/failure-injection coverage
for src/core/portfolio.

pytest collects this module when invoked with an explicit path
(`pytest tests/unit/core/portfolio/__init__.py`) even though it is not
matched by the default `test_*.py` discovery glob — see task-9982
(tests/unit/core/loader/__init__.py) and task-9987
(tests/unit/core/notifications/__init__.py) for the same pattern.

Scope: most src/core/portfolio/*.py modules already carry dedicated
DEEPEN-level negative/failure-injection/perf tests in sibling test_*.py
files (test_accounting.py, test_aggregation.py, test_rebalance.py,
test_mandate_binding.py, test_engine_v2.py, test_portfolio_contracts.py).
This module targets the two gaps that survive that audit:

1. `PortfolioAggregate` (state_input.py) defines no field validators at
   all (unlike its sibling `PortfolioStateInput`/`PortfolioConfig`, which
   both reject float input) — its only enforceable invariant is pydantic's
   own required-field check, which no existing test exercises.
2. `PortfolioEngine.allocate` (engine.py) has a failure-injection test for
   a corrupted `total_equity` lookup but not for a corrupted
   `allocated_capital` lookup on the BUY path — a different line
   (`current_portfolio_state["allocated_capital"]`) that must equally
   fail closed instead of silently defaulting.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.portfolio.engine import PortfolioEngine
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput
from src.data.models.trading import OrderSide


def _aggregate_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "total_equity": Decimal("10000"),
        "per_symbol_pct": {"BTC/USDT": Decimal("10")},
        "per_strategy_pct": {"strat-1": Decimal("10")},
        "total_exposure_pct": Decimal("10"),
        "cash_pct": Decimal("90"),
        "as_of": datetime(2026, 9, 9, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_portfolio_aggregate_missing_total_equity_raises() -> None:
    kwargs = _aggregate_kwargs()
    del kwargs["total_equity"]
    with pytest.raises(ValidationError):
        PortfolioAggregate(**kwargs)


def test_portfolio_aggregate_missing_as_of_raises() -> None:
    kwargs = _aggregate_kwargs()
    del kwargs["as_of"]
    with pytest.raises(ValidationError):
        PortfolioAggregate(**kwargs)


def test_portfolio_state_input_missing_portfolio_config_raises() -> None:
    """`portfolio_config` has no default -- a caller that forgets to attach
    a sizing config must be rejected, not silently proceed without one."""
    with pytest.raises(ValidationError):
        PortfolioStateInput(  # type: ignore[call-arg]
            allocated_capital=Decimal("1000"),
            position_quantity=Decimal("0"),
            current_price=Decimal("50000"),
            total_equity=Decimal("10000"),
            cash_available=Decimal("9000"),
        )


class _Signal:
    def __init__(self, symbol: str, strategy_id: str, direction: OrderSide) -> None:
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.direction = direction


class _BrokenAllocatedCapitalState(Mapping[str, Any]):
    """Stand-in for a `current_portfolio_state` whose `allocated_capital`
    entry fails to hydrate (e.g. a corrupted FD-16.1 limit lookup) while
    every other key reads fine."""

    _KEYS = ("current_price", "position_quantity", "allocated_capital", "total_equity")

    def __getitem__(self, key: str) -> Any:
        if key == "current_price":
            return Decimal("50000")
        if key == "position_quantity":
            return Decimal("0")
        if key == "allocated_capital":
            raise RuntimeError("upstream allocated_capital limit lookup corrupted")
        if key == "total_equity":
            return Decimal("10000")
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._KEYS)

    def __len__(self) -> int:
        return len(self._KEYS)


def test_failure_injection_engine_allocate_corrupted_allocated_capital_propagates() -> None:
    """A corrupted `allocated_capital` read must propagate (fail-closed),
    not be swallowed into a zero/None allocation decision."""
    engine = PortfolioEngine()
    signal = _Signal("BTC-USDT", "strat-1", OrderSide.BUY)

    with pytest.raises(RuntimeError, match="allocated_capital limit lookup corrupted"):
        engine.allocate(signal, _BrokenAllocatedCapitalState())  # type: ignore[arg-type]
