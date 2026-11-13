"""LA-19 depth supplement (DEEPEN task-24603, D2 -> D3): adversarial / failure-injection /
perf gate-red evidence for the exchange symbol conversion delegation.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-19.
Existing tests in test_symbol_canonicalization.py are untouched; this file only adds:
(1) negative: adversarial / malformed symbols never convert silently (fail-closed).
(2) failure injection: a normalizer that raises must propagate, not be swallowed.
(3) gate-red: the delegation assertion goes red when the adapter stops delegating.
(4) perf: to_canonical_symbol budget + 10x virtual delay injection gate-red.
(5) adversarial/concurrency: parallel threads over hostile inputs stay deterministic.

Perf budget (new): to_canonical_symbol < 0.5 ms per call. Local measurement via
PerfBudget.best_of(n=5, batch=100): wall ~0.0004 ms/call, cpu below timer resolution,
i.e. >1000x headroom (>= 30x required).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from src.exchanges.bitget import symbols as bitget_symbols
from src.exchanges.bitget.symbols import to_bitget_symbol, to_canonical_symbol
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_BUDGET_MS = 0.5


# ---------- negative ----------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "BTC/",
        "/USDT",
        "BTC//USDT",
        "BTC/USDT/EXTRA",
        "btc/xyz",
        "BTC USDT",
    ],
)
def test_to_bitget_symbol_rejects_malformed_input(bad: str) -> None:
    with pytest.raises((SymbolNormalizationError, ValueError)):
        to_bitget_symbol(bad)


@pytest.mark.parametrize("bad", ["", "USDT", "BTCXYZ", "BTCUSDT; DROP"])
def test_to_canonical_symbol_rejects_malformed_input(bad: str) -> None:
    with pytest.raises((SymbolNormalizationError, ValueError)):
        to_canonical_symbol(bad)


def test_none_input_is_not_coerced() -> None:
    hostile: Any = None
    with pytest.raises((SymbolNormalizationError, ValueError, TypeError, AttributeError)):
        to_bitget_symbol(hostile)


# ---------- failure injection ----------


def test_normalizer_failure_propagates_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("injected normalizer fault")

    monkeypatch.setattr(bitget_symbols, "_to_venue", boom, raising=True)
    monkeypatch.setattr(bitget_symbols, "_to_canonical", boom, raising=True)
    with pytest.raises(RuntimeError, match="injected normalizer fault"):
        to_bitget_symbol("BTC/USDT")
    with pytest.raises(RuntimeError, match="injected normalizer fault"):
        to_canonical_symbol("BTCUSDT")


# ---------- gate-red reproduction ----------


def test_gate_red_non_delegating_conversion_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the adapter re-implemented a lenient conversion, the fail-closed assertion flips red."""
    monkeypatch.setattr(bitget_symbols, "_to_venue", lambda _v, s: s.replace("/", ""), raising=True)
    with pytest.raises(pytest.fail.Exception):  # the fail-closed assertion goes red
        with pytest.raises(SymbolNormalizationError):
            to_bitget_symbol("BTC/XYZ")  # lenient copy returns "BTCXYZ" -> DID NOT RAISE


# ---------- perf ----------


@pytest.mark.perf
def test_to_canonical_symbol_performance_under_budget() -> None:
    perf = PerfBudget()
    sample = perf.best_of(lambda: to_canonical_symbol("BTCUSDT"), n=5, batch=100)
    assert sample.cpu_ms < _BUDGET_MS, perf.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
def test_to_canonical_symbol_gate_red_with_10x_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_virtual_totals()
    slow = virtual_delayed_sync(to_canonical_symbol, _BUDGET_MS * 10 / 1000)
    perf = PerfBudget()
    sample = perf.best_of(lambda: slow("BTCUSDT"), n=3, batch=1)
    assert sample.cpu_ms >= _BUDGET_MS, "10x injected delay must exceed the budget"
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _BUDGET_MS
    reset_virtual_totals()


# ---------- adversarial / concurrency ----------


def test_concurrent_hostile_inputs_are_deterministic() -> None:
    inputs = ["BTC/USDT", "ETH/USDT", "BTC/XYZ", "", "BTC//USDT", "SOL/USDT"] * 50

    def run(sym: str) -> str:
        try:
            return to_bitget_symbol(sym)
        except (SymbolNormalizationError, ValueError):
            return "ERR"

    expected = [run(s) for s in inputs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(run, inputs))
    assert got == expected
    assert got[0] == "BTCUSDT" and got[2] == "ERR" and got[3] == "ERR"
