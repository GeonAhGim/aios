"""EM-12 -- `domain/tca/benchmarks.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-12 DoD (a)-(f).
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.data.models.market_data import Candle
from src.foundation.ems.domain.tca.benchmarks import (
    EmptyBarsError,
    EmptyFillsError,
    Fill,
    InvalidFillError,
    arrival_price,
    close_price,
    compute_vwap,
)

_BENCHMARKS_PATH = (
    Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/tca/benchmarks.py"
)


def _candle(close: Decimal, close_time: datetime) -> Candle:
    return Candle(
        symbol="BTC-USD",
        exchange="test",
        timeframe="1m",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        open_time=close_time,
        close_time=close_time,
    )


# -- (a) exactly three public functions --------------------------------------


def test_module_exposes_exactly_three_public_functions() -> None:
    tree = ast.parse(_BENCHMARKS_PATH.read_text(encoding="utf-8"))
    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert public_functions == {"arrival_price", "compute_vwap", "close_price"}


# -- (b) VWAP exact-value assertion (no pytest.approx, no float) -------------


def test_compute_vwap_returns_the_exact_decimal_value() -> None:
    fills = [
        Fill(price=Decimal("100"), qty=Decimal("10")),
        Fill(price=Decimal("110"), qty=Decimal("30")),
    ]
    result = compute_vwap(fills)
    assert result == Decimal("107.5")
    assert isinstance(result, Decimal)


def test_benchmarks_module_never_calls_float() -> None:
    source = _BENCHMARKS_PATH.read_text(encoding="utf-8")
    assert "float(" not in source, "benchmarks.py: float( found -- VWAP must stay Decimal-only."


# -- (c) reject inputs: empty fills, non-positive qty, non-positive price ---


def test_empty_fills_is_rejected_not_reported_as_zero_cost() -> None:
    with pytest.raises(EmptyFillsError, match="empty"):
        compute_vwap([])


@pytest.mark.parametrize("bad_qty", [Decimal("0"), Decimal("-1")])
def test_non_positive_qty_is_rejected(bad_qty: Decimal) -> None:
    fills = [Fill(price=Decimal("100"), qty=bad_qty)]
    with pytest.raises(InvalidFillError, match="qty"):
        compute_vwap(fills)


@pytest.mark.parametrize("bad_price", [Decimal("0"), Decimal("-1")])
def test_non_positive_price_is_rejected(bad_price: Decimal) -> None:
    fills = [Fill(price=bad_price, qty=Decimal("10"))]
    with pytest.raises(InvalidFillError, match="price"):
        compute_vwap(fills)


def test_one_bad_fill_among_good_fills_still_rejects_the_whole_batch() -> None:
    fills = [
        Fill(price=Decimal("100"), qty=Decimal("10")),
        Fill(price=Decimal("110"), qty=Decimal("0")),
    ]
    with pytest.raises(InvalidFillError):
        compute_vwap(fills)


# -- (d) contracts/v1.py TcaResult is untouched by this leaf ------------------


def test_contracts_v1_defines_tca_result_with_the_settled_bps_fields() -> None:
    from src.foundation.ems.contracts.v1 import TcaResult

    assert set(TcaResult.model_fields) >= {
        "arrival_bps",
        "vwap_bps",
        "impact_bps",
        "fees_bps",
        "opportunity_bps",
    }


# -- (e) arrival_price: pure passthrough, same input -> same output ---------


def test_arrival_price_returns_the_same_output_for_the_same_input() -> None:
    reference = Decimal("42000.5")
    assert arrival_price(reference) == arrival_price(reference) == reference


def test_benchmarks_module_reads_no_wall_clock() -> None:
    tree = ast.parse(_BENCHMARKS_PATH.read_text(encoding="utf-8"))
    now_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "today"}
    ]
    assert not now_calls, "benchmarks.py: wall-clock read found (datetime.now/...)."


# -- close_price --------------------------------------------------------------


def test_close_price_returns_the_close_of_the_last_bar() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [_candle(Decimal("100"), t0), _candle(Decimal("105"), t0)]
    assert close_price(bars) == Decimal("105")


def test_close_price_rejects_empty_bars() -> None:
    with pytest.raises(EmptyBarsError, match="empty"):
        close_price([])


# -- (f) file size discipline -------------------------------------------------


def test_benchmarks_module_is_at_most_260_lines() -> None:
    line_count = len(_BENCHMARKS_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 260, f"benchmarks.py has {line_count} lines, exceeding the leaf cap."
