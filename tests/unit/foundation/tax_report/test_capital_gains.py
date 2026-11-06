"""U-9 exact aggregation, malformed input and determinism evidence."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from random import Random
from typing import Any

import pytest

import tests.unit.foundation.tax_report.test_capital_gains as _mod
from src.data.models.base import Currency
from src.foundation.tax_report.domain.capital_gains import (
    AssetClass,
    RealizedGainEntry,
    summarize_realized_gains,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync, virtual_totals_ns

START = date(2026, 1, 1)
END = date(2026, 12, 31)
CLOSED = datetime(2026, 6, 1, tzinfo=UTC)


def entry(amount: str = "1", asset: AssetClass = AssetClass.CRYPTO) -> RealizedGainEntry:
    return RealizedGainEntry(asset, Decimal(amount), Currency.KRW, CLOSED)


@pytest.fixture
def gains() -> list[RealizedGainEntry]:
    return [
        entry("100.125", AssetClass.DOMESTIC_STOCK),
        entry("-30.005", AssetClass.DOMESTIC_STOCK),
        entry("200.0001", AssetClass.FOREIGN_STOCK),
        entry("-50.1234"),
    ]


def test_hand_calculated_all_asset_classes(gains: list[RealizedGainEntry]) -> None:
    result = summarize_realized_gains(gains, START, END)
    assert result.by_asset_class == {
        AssetClass.DOMESTIC_STOCK: Decimal("70.120"),
        AssetClass.FOREIGN_STOCK: Decimal("200.0001"),
        AssetClass.CRYPTO: Decimal("-50.1234"),
    }
    assert result.total == Decimal("219.9967")
    assert (result.period_start, result.period_end) == (START, END)


def test_inclusive_period_boundaries() -> None:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    last = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    entries = [
        replace(entry(str(i)), closed_at=at)
        for i, at in enumerate(
            [first - timedelta(microseconds=1), first, last, last + timedelta(microseconds=1)], 1
        )
    ]
    assert summarize_realized_gains(entries, START, END).total == Decimal("5")


def test_empty_has_all_three_zero_keys() -> None:
    result = summarize_realized_gains([], START, END)
    assert result.total == Decimal("0")
    assert result.by_asset_class == dict.fromkeys(AssetClass, Decimal("0"))
    assert all(isinstance(value, Decimal) for value in result.by_asset_class.values())


def test_same_day_and_all_excluded() -> None:
    assert summarize_realized_gains([entry("-2")], CLOSED.date(), CLOSED.date()).total == -2
    assert summarize_realized_gains([entry()], START, START).by_asset_class == dict.fromkeys(
        AssetClass, Decimal("0")
    )


@pytest.mark.parametrize("seed", [7, 101, 8109])
def test_randomized_invariant_and_replay(seed: int) -> None:
    rng = Random(seed)
    entries = [
        entry(str(Decimal(rng.randint(-100000, 100000)) / 1000), rng.choice(list(AssetClass)))
        for _ in range(300)
    ]
    result = summarize_realized_gains(entries, START, END)
    assert result.total == sum(result.by_asset_class.values())
    assert result.total == sum(item.realized_pnl_base for item in entries)
    rng.shuffle(entries)
    assert summarize_realized_gains(entries, START, END) == result


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("asset_class", "CRYPTO", TypeError),
        ("asset_class", None, TypeError),
        ("closed_at", None, TypeError),
        ("closed_at", datetime(2026, 1, 1), ValueError),
        ("closed_at", CLOSED.astimezone(timezone(timedelta(hours=9))), ValueError),
        ("realized_pnl_base", 1.5, TypeError),
        ("realized_pnl_base", Decimal("NaN"), ValueError),
        ("realized_pnl_base", Decimal("Infinity"), ValueError),
        ("realized_pnl_base", Decimal("sNaN"), ValueError),
        ("currency", "KRW", TypeError),
    ],
)
def test_malformed_entry(field: str, value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        replace(entry(), **{field: value})


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        (END, START, ValueError),
        (None, END, TypeError),
        (CLOSED, END, TypeError),
    ],
)
def test_invalid_period(start: Any, end: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        summarize_realized_gains([], start, end)


def test_non_entry_is_rejected() -> None:
    malformed: Any = {"asset_class": "CRYPTO"}
    with pytest.raises(TypeError, match="RealizedGainEntry"):
        summarize_realized_gains([malformed], START, END)


def test_mixed_base_currency_is_rejected() -> None:
    foreign = replace(entry(), currency=Currency.USDT)
    with pytest.raises(ValueError, match="base currency"):
        summarize_realized_gains([entry(), foreign], START, END)
    assert (
        summarize_realized_gains(
            [entry(), replace(foreign, closed_at=CLOSED.replace(year=2025))],
            START,
            END,
        ).total
        == 1
    )


def test_injected_corrupt_record_fails_even_outside_period() -> None:
    corrupt = replace(entry(), closed_at=CLOSED.replace(year=2025))
    object.__setattr__(corrupt, "asset_class", "CRYPTO")
    with pytest.raises(TypeError, match="asset_class"):
        summarize_realized_gains([entry(), corrupt], START, END)


def test_low_precision_cancellation_has_no_residual() -> None:
    entries = [
        entry("1000000000000000000000000000000"),
        entry("0.000000001"),
        entry("-1000000000000000000000000000000"),
    ]
    with localcontext() as context:
        context.prec = 3
        result = summarize_realized_gains(entries, START, END)
        assert result.total == Decimal("0.000000001")
        assert context.prec == 3
        assert result.total == sum(result.by_asset_class.values())


def test_inputs_and_results_are_independent(gains: list[RealizedGainEntry]) -> None:
    original = list(gains)
    first = summarize_realized_gains(gains, START, END)
    first.by_asset_class[AssetClass.CRYPTO] = Decimal("999")
    assert gains == original
    assert summarize_realized_gains(gains, START, END).total == Decimal("219.9967")


@pytest.mark.perf
def test_5000_entries_p95_budget(perf_budget: PerfBudget) -> None:
    # ADR D2: use the 5k-row/200ms budget as an upper bound for pure aggregation.
    entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]

    def measure() -> Any:
        return summarize_realized_gains(entries, START, END)

    samples = perf_budget.samples(measure, n=20, batch=8)
    for s in samples:
        assert s.result.total == Decimal("-61.5000")
    sorted_cpu = sorted(s.cpu_ms for s in samples)
    assert sorted_cpu[18] < 200
    # best_of minimum must also be under budget (proves 0-convergence is not masking slow paths)
    best = perf_budget.best_of(measure, n=5, batch=8)
    assert best.cpu_ms < 200, f"best_of minimum cpu_ms={best.cpu_ms:.3f} exceeds 200ms budget"


# --- gate-red reproduction and negative tests (task-22370) ---


def test_gate_red_repro_10x_delay_raises(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject 10x budget (2 s) via virtual_delayed_sync and expect AssertionError."""
    reset_virtual_totals()
    original = summarize_realized_gains
    patched = virtual_delayed_sync(original, 2.0)
    monkeypatch.setattr(_mod, "summarize_realized_gains", patched)
    try:
        entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]

        def measure() -> Any:
            return _mod.summarize_realized_gains(entries, START, END)

        with pytest.raises(AssertionError):
            perf_budget.assert_within(measure, budget_ms=200, n=3, batch=8)
    finally:
        reset_virtual_totals()


def test_mutation_injection_fails_green(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a function that corrupts the result; the total assertion must turn red."""
    reset_virtual_totals()
    original = summarize_realized_gains
    monkeypatch.setattr(
        _mod,
        "summarize_realized_gains",
        lambda *a, **k: _mutated_result(original(*a, **k)),
    )
    try:
        entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]

        def measure() -> Any:
            return _mod.summarize_realized_gains(entries, START, END)

        samples = perf_budget.samples(measure, n=3, batch=8)
        with pytest.raises(AssertionError):
            for s in samples:
                assert s.result.total == Decimal("-61.5000")
    finally:
        reset_virtual_totals()


def _mutated_result(result: object) -> object:
    """Return a copy of result with total set to a wrong value."""
    return replace(result, total=Decimal("9999"))


def test_reduced_input_count_fails(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Supply 4999 entries instead of 5000; the total must not match."""
    reset_virtual_totals()
    original = summarize_realized_gains
    monkeypatch.setattr(
        _mod,
        "summarize_realized_gains",
        lambda *a, **k: original(a[0][:4999] if a else [], *a[1:]),
    )
    try:
        entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]

        def measure() -> Any:
            return _mod.summarize_realized_gains(entries, START, END)

        samples = perf_budget.samples(measure, n=3, batch=8)
        with pytest.raises(AssertionError):
            for s in samples:
                assert s.result.total == Decimal("-61.5000")
    finally:
        reset_virtual_totals()


def test_injection_ledger_delta_matches(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that the virtual ledger actually moves by the expected amount."""
    reset_virtual_totals()
    original = summarize_realized_gains
    inject_seconds = 2.0
    patched = virtual_delayed_sync(original, inject_seconds)
    monkeypatch.setattr(_mod, "summarize_realized_gains", patched)
    try:
        entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]
        before_cpu, before_wall = virtual_totals_ns()
        _mod.summarize_realized_gains(entries, START, END)
        after_cpu, after_wall = virtual_totals_ns()
        delta_cpu_ns = after_cpu - before_cpu
        # each call adds inject_seconds as both cpu and wall delay
        expected_delta_ns = round(inject_seconds * 1e9)
        assert delta_cpu_ns == expected_delta_ns, (
            f"ledger delta {delta_cpu_ns} ns != expected {expected_delta_ns} ns"
        )
    finally:
        reset_virtual_totals()
