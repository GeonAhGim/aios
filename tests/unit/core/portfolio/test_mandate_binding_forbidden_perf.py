"""Forbidden-asset path of mandate_binding.bind(): negative, injection and gate-red tests.

Companion to test_mandate_binding.py::test_forbidden_asset_denies_exactly (kept separate so the
main file stays under the 500-line ratchet). The perf assertion there is reused unchanged here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.portfolio.mandate_binding import POLICY_FORBIDDEN_ASSET, bind
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync
from tests.unit.core.portfolio.test_mandate_binding import agg, mandate

_BUDGET_MS = 5.0


def _forbidden_call() -> None:
    bind(
        qty=Decimal("10"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )


def test_other_symbol_not_denied_as_forbidden() -> None:
    result = bind(
        qty=Decimal("1"),
        price=Decimal("250"),
        symbol="BTC/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert POLICY_FORBIDDEN_ASSET not in result.reasons
    assert result.denied is False


def test_forbidden_asset_never_clamps_to_nonzero_quantity() -> None:
    result = bind(
        qty=Decimal("1000000"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert result.denied is True
    assert result.quantity == Decimal("0")


def test_forbidden_asset_deny_is_case_sensitive_exact_match() -> None:
    result = bind(
        qty=Decimal("1"),
        price=Decimal("250"),
        symbol="SANCTIONED/USDT2",
        agg=agg(),
        mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
    )
    assert POLICY_FORBIDDEN_ASSET not in result.reasons


@pytest.mark.perf
def test_forbidden_asset_normal_input_is_green(perf_budget: PerfBudget) -> None:
    sample = perf_budget.best_of(_forbidden_call, n=5, batch=100)
    assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
def test_forbidden_asset_goes_red_with_10x_virtual_delay(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """Failure injection: a 10x-budget virtual CPU delay must turn the same assertion red."""
    import src.core.portfolio.mandate_binding as mod

    monkeypatch.setattr(mod, "bind", virtual_delayed_sync(mod.bind, _BUDGET_MS * 10 / 1000))

    def _call_once() -> None:
        mod.bind(
            qty=Decimal("10"),
            price=Decimal("250"),
            symbol="SANCTIONED/USDT",
            agg=agg(),
            mandate=mandate(forbidden_assets=["SANCTIONED/USDT"]),
        )

    sample = perf_budget.best_of(_call_once, n=5, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)
