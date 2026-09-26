"""RD-11 -- `domain/macro_series.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-11
DoD ("빈도·단위·발표지연 정규화").
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.foundation.research_data.domain.macro_series import (
    FrequencyNormalizationError,
    MacroFrequency,
    PeriodParseError,
    UnitNormalizationError,
    ValueParseError,
    compute_known_at,
    normalize_macro_observation,
    normalize_unit,
    period_end,
)

# --- normalize_unit -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw_unit", "expected_unit", "expected_scale"),
    [
        ("%", "percent", 1),
        ("퍼센트", "percent", 1),
        ("지수", "index", 1),
        ("포인트", "index", 1),
        ("원", "krw", 1),
        ("억원", "krw", 100_000_000),
        ("십억원", "krw", 1_000_000_000),
        ("달러", "usd", 1),
        ("백만달러", "usd", 1_000_000),
        ("명", "count", 1),
    ],
)
def test_normalize_unit_known_shapes(
    raw_unit: str, expected_unit: str, expected_scale: int
) -> None:
    result = normalize_unit(raw_unit)
    assert result.unit == expected_unit
    assert result.scale == Decimal(expected_scale)


def test_normalize_unit_rejects_empty_string() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("")


def test_normalize_unit_rejects_unknown_base_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("억파운드")


def test_normalize_unit_rejects_magnitude_prefix_without_base_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("천")


# --- period_end ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_period", "frequency", "expected"),
    [
        ("2024", MacroFrequency.ANNUAL, date(2024, 12, 31)),
        ("2024S1", MacroFrequency.SEMIANNUAL, date(2024, 6, 30)),
        ("20242", MacroFrequency.SEMIANNUAL, date(2024, 12, 31)),
        ("2024Q1", MacroFrequency.QUARTERLY, date(2024, 3, 31)),
        ("20244", MacroFrequency.QUARTERLY, date(2024, 12, 31)),
        ("202402", MacroFrequency.MONTHLY, date(2024, 2, 29)),  # leap year
        ("20240115", MacroFrequency.DAILY, date(2024, 1, 15)),
    ],
)
def test_period_end_known_shapes(
    raw_period: str, frequency: MacroFrequency, expected: date
) -> None:
    assert period_end(raw_period, frequency) == expected


def test_period_end_rejects_mismatched_format() -> None:
    with pytest.raises(PeriodParseError):
        period_end("2024", MacroFrequency.MONTHLY)


def test_period_end_rejects_invalid_calendar_date() -> None:
    with pytest.raises(PeriodParseError):
        period_end("20240230", MacroFrequency.DAILY)  # Feb 30 does not exist


# --- compute_known_at ------------------------------------------------------


def test_compute_known_at_adds_lag_at_utc_midnight() -> None:
    known_at = compute_known_at(date(2024, 1, 31), publication_lag_days=15)
    assert known_at == datetime(2024, 2, 15, tzinfo=timezone.utc)
    assert known_at.tzinfo is timezone.utc


def test_compute_known_at_rejects_negative_lag() -> None:
    with pytest.raises(ValueError):
        compute_known_at(date(2024, 1, 31), publication_lag_days=-1)


# --- normalize_macro_observation (composition) ------------------------------


def test_normalize_macro_observation_composes_all_axes() -> None:
    observation = normalize_macro_observation(
        source_id="ECOS",
        series_id="722Y001:0101000",
        raw_period="202401",
        frequency=MacroFrequency.MONTHLY,
        raw_value="3.5",
        raw_unit="%",
        seasonally_adjusted=True,
        publication_lag_days=20,
    )
    assert observation.source_id == "ECOS"
    assert observation.period == date(2024, 1, 31)
    assert observation.frequency is MacroFrequency.MONTHLY
    assert observation.value == Decimal("3.5")
    assert observation.unit == "percent"
    assert observation.seasonally_adjusted is True
    assert observation.known_at == datetime(2024, 2, 20, tzinfo=timezone.utc)


def test_normalize_macro_observation_applies_unit_scale_to_value() -> None:
    observation = normalize_macro_observation(
        source_id="KOSIS",
        series_id="DT_1YL20631:T10",
        raw_period="2024",
        frequency=MacroFrequency.ANNUAL,
        raw_value="1,234",
        raw_unit="억원",
        seasonally_adjusted=False,
        publication_lag_days=0,
    )
    assert observation.value == Decimal("123400000000")


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_normalize_macro_observation_rejects_empty_source_id() -> None:
    with pytest.raises(ValueError):
        normalize_macro_observation(
            source_id="",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="1",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_non_numeric_value() -> None:
    with pytest.raises(ValueParseError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="not-a-number",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_unknown_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="1",
            raw_unit="파운드",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_period_frequency_mismatch() -> None:
    with pytest.raises(PeriodParseError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024Q1",
            frequency=MacroFrequency.MONTHLY,
            raw_value="1",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


# --- failure injection: a fabricated frequency enum value the module never

def test_period_end_fails_closed_for_unsupported_frequency_value() -> None:
    """Failure injection -- `object.__new__` fabricates a `MacroFrequency`
    member that bypasses every branch in `period_end`, exercising its
    final `FrequencyNormalizationError` fail-closed fallthrough (a state
    that cannot occur through the public enum but must still not silently
    fall through to `None`/a guessed date)."""
    bogus = object.__new__(MacroFrequency)
    object.__setattr__(bogus, "_name_", "BOGUS")
    object.__setattr__(bogus, "_value_", "bogus")
    with pytest.raises(FrequencyNormalizationError):
        period_end("2024", bogus)


# --- numeric performance assertion (ADR-2026-09-09-C budget: pure CPU-bound
# normalization of a realistic batch must clear a fixed throughput floor) ---


@pytest.mark.perf
def test_normalize_macro_observation_throughput_floor() -> None:
    iterations = 2_000
    started = time.perf_counter()
    for i in range(iterations):
        normalize_macro_observation(
            source_id="ECOS",
            series_id=f"722Y001:{i:07d}",
            raw_period="202401",
            frequency=MacroFrequency.MONTHLY,
            raw_value="3.5",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=20,
        )
    elapsed = time.perf_counter() - started
    throughput = iterations / elapsed
    assert throughput > 5_000, f"normalize_macro_observation throughput too low: {throughput:.0f}/s"
