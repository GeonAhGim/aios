"""U-9 exact aggregation, malformed input and determinism evidence."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from random import Random
from time import perf_counter
from typing import Any

import pytest

from src.data.models.base import Currency
from src.foundation.tax_report.domain.capital_gains import (
    AssetClass,
    RealizedGainEntry,
    summarize_realized_gains,
)

START = date(2026, 1, 1)
END = date(2026, 12, 31)
CLOSED = datetime(2026, 6, 1, tzinfo=timezone.utc)


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
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    last = datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
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
    with pytest.raises(FrozenInstanceError):
        gains[0].currency = Currency.USDT


@pytest.mark.perf
def test_5000_entries_p95_budget() -> None:
    # ADR D2: use the 5k-row/200ms budget as an upper bound for pure aggregation.
    entries = [entry("-0.0123", list(AssetClass)[i % 3]) for i in range(5000)]
    samples = []
    for _ in range(20):
        started = perf_counter()
        result = summarize_realized_gains(entries, START, END)
        samples.append(perf_counter() - started)
        assert result.total == Decimal("-61.5000")
    assert sorted(samples)[18] < 0.2
