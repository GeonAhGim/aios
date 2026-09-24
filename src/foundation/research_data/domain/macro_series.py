"""RD-11 -- domain/macro_series.py: macro time-series normalization
(frequency / unit / seasonal-adjustment flag / publication lag).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.2
`domain/macro_series.py` ("거시 시계열 정규화(빈도·단위·계절조정 플래그·발표
지연)"), sec.9 RD-11 DoD ("빈도·단위·발표지연 정규화").

Collector adapters (`adapters/sources/{ecos,kosis}.py`) each parse their own
wire format into plain strings (a raw period code, a raw numeric value
string, a raw unit string) and hand them to `normalize_macro_observation`
here -- this module never talks to a source's HTTP API itself (I/O-free,
L0-2) and never guesses at a source's response field names; that guessing
risk is fully contained in the adapter layer instead.

Every unrecognized input (an unknown unit string, a period code that does
not match its declared frequency's format, a non-numeric value) is rejected
with a typed error rather than coerced with a best-effort guess -- I2's
"do not guess a wire fact" posture applies just as much to a public
government statistics API as it does to an undocumented exchange endpoint.
"""
from __future__ import annotations

import calendar
import enum
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

__all__ = [
    "MacroFrequency",
    "FrequencyNormalizationError",
    "PeriodParseError",
    "UnitNormalizationError",
    "ValueParseError",
    "NormalizedUnit",
    "MacroObservation",
    "normalize_unit",
    "period_end",
    "compute_known_at",
    "normalize_macro_observation",
]


class MacroFrequency(enum.Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


class FrequencyNormalizationError(ValueError):
    """An unrecognized frequency was requested -- fail-closed instead of
    guessing a period format for it (I2)."""


class PeriodParseError(ValueError):
    """`raw_period` does not match the format expected for the declared
    `MacroFrequency`."""


class UnitNormalizationError(ValueError):
    """`raw_unit` could not be resolved to a known canonical unit + scale."""


class ValueParseError(ValueError):
    """`raw_value` could not be parsed as a decimal number."""


@dataclass(frozen=True)
class NormalizedUnit:
    unit: str
    scale: Decimal


# Korean magnitude prefixes combined with a base unit (e.g. "십억원",
# "백만달러"). This is a stable, publicly documented feature of the Korean
# numeral system, not a source-specific undocumented wire contract, so it is
# not subject to the ratchet-allow/NotImplementedError posture that guards
# actually-unverified external facts (adapters/sources/{ecos,kosis}.py carry
# that risk instead, for their own response field names).
# Ordered longest-prefix-first so "십억" is tried before "억"/"백만" etc.
_MAGNITUDE_PREFIXES: tuple[tuple[str, int], ...] = (
    ("십억", 1_000_000_000),
    ("백만", 1_000_000),
    ("조", 1_000_000_000_000),
    ("억", 100_000_000),
    ("만", 10_000),
    ("천", 1_000),
    ("백", 100),
)

_UNIT_ALIASES: dict[str, str] = {
    "%": "percent",
    "％": "percent",
    "퍼센트": "percent",
    "지수": "index",
    "포인트": "index",
    "pt": "index",
    "원": "krw",
    "krw": "krw",
    "달러": "usd",
    "usd": "usd",
    "명": "count",
    "건": "count",
}

_QUARTER_MONTH_END: dict[int, tuple[int, int]] = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_SEMIANNUAL_MONTH_END: dict[int, tuple[int, int]] = {1: (6, 30), 2: (12, 31)}

_ANNUAL_RE = re.compile(r"(\d{4})")
_SEMIANNUAL_RE = re.compile(r"(\d{4})S?([12])")
_QUARTERLY_RE = re.compile(r"(\d{4})Q?([1-4])")
_MONTHLY_RE = re.compile(r"(\d{4})(\d{2})")
_DAILY_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")


def normalize_unit(raw_unit: str) -> NormalizedUnit:
    """Resolve `raw_unit` (e.g. `"%"`, `"억원"`, `"십억달러"`, `"지수"`) to a
    canonical unit name plus the `Decimal` multiplier that converts the raw
    numeric value into that base unit.

    Fails closed (`UnitNormalizationError`) on anything not in the known
    alias/magnitude tables -- there is no fallback that returns `scale=1`
    for an unrecognized string, since that would silently under-report a
    value by whatever magnitude prefix was missed.
    """
    candidate = raw_unit.strip()
    if not candidate:
        raise UnitNormalizationError("empty unit string")
    scale = 1
    remainder = candidate
    for prefix, multiplier in _MAGNITUDE_PREFIXES:
        if remainder.startswith(prefix):
            scale = multiplier
            remainder = remainder[len(prefix) :].strip()
            break
    if not remainder:
        raise UnitNormalizationError(f"magnitude prefix without a base unit: {raw_unit!r}")
    canonical = _UNIT_ALIASES.get(remainder) or _UNIT_ALIASES.get(remainder.lower())
    if canonical is None:
        raise UnitNormalizationError(f"unknown unit: {raw_unit!r}")
    return NormalizedUnit(unit=canonical, scale=Decimal(scale))


def period_end(raw_period: str, frequency: MacroFrequency) -> date:
    """Parse a source's raw period code into the calendar date on which
    that reporting period ends, given its already-known `MacroFrequency`.

    Accepts both a bare digit-run encoding (`"20241"` for 2024 Q1) and a
    letter-marked encoding (`"2024Q1"`) for quarterly/semiannual periods,
    since collector sources are not consistent with each other on this --
    the frequency itself must always be supplied by the caller (it is never
    inferred from the string here), so there is no ambiguity between, say,
    a 5-digit quarterly code and a 5-digit anything else.
    """
    candidate = raw_period.strip()
    if frequency is MacroFrequency.ANNUAL:
        match = _ANNUAL_RE.fullmatch(candidate)
        if not match:
            raise PeriodParseError(f"expected YYYY for ANNUAL, got {raw_period!r}")
        return date(int(match.group(1)), 12, 31)
    if frequency is MacroFrequency.SEMIANNUAL:
        match = _SEMIANNUAL_RE.fullmatch(candidate)
        if not match:
            raise PeriodParseError(f"expected YYYY[S]H for SEMIANNUAL, got {raw_period!r}")
        year, half = int(match.group(1)), int(match.group(2))
        month, day = _SEMIANNUAL_MONTH_END[half]
        return date(year, month, day)
    if frequency is MacroFrequency.QUARTERLY:
        match = _QUARTERLY_RE.fullmatch(candidate)
        if not match:
            raise PeriodParseError(f"expected YYYY[Q]# for QUARTERLY, got {raw_period!r}")
        year, quarter = int(match.group(1)), int(match.group(2))
        month, day = _QUARTER_MONTH_END[quarter]
        return date(year, month, day)
    if frequency is MacroFrequency.MONTHLY:
        match = _MONTHLY_RE.fullmatch(candidate)
        if not match:
            raise PeriodParseError(f"expected YYYYMM for MONTHLY, got {raw_period!r}")
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise PeriodParseError(f"invalid month in {raw_period!r}")
        return date(year, month, calendar.monthrange(year, month)[1])
    if frequency is MacroFrequency.DAILY:
        match = _DAILY_RE.fullmatch(candidate)
        if not match:
            raise PeriodParseError(f"expected YYYYMMDD for DAILY, got {raw_period!r}")
        year, month, day = (int(group) for group in match.groups())
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise PeriodParseError(f"invalid calendar date: {raw_period!r}") from exc
    raise FrequencyNormalizationError(f"unsupported frequency: {frequency!r}")


def compute_known_at(period_ended: date, *, publication_lag_days: int) -> datetime:
    """`known_at` (RD-A1 point-in-time semantics) for a macro observation is
    the reporting period's end date plus the source's declared publication
    lag, at UTC midnight -- a backtest reading `as_of` any earlier than this
    could not have known this figure yet.

    A negative lag is rejected rather than silently treated as zero: it
    would mean the fact becomes known *before* the period it describes even
    ends, which is exactly the future-leak RD-A1 exists to prevent.
    """
    if publication_lag_days < 0:
        raise ValueError(
            "publication_lag_days must be >= 0 (a negative lag would leak a "
            "future fact before its own reporting period ends)"
        )
    known_date = period_ended + timedelta(days=publication_lag_days)
    return datetime(known_date.year, known_date.month, known_date.day, tzinfo=timezone.utc)


def _parse_value(raw_value: str) -> Decimal:
    candidate = raw_value.strip().replace(",", "")
    if not candidate:
        raise ValueParseError("empty value string")
    try:
        return Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueParseError(f"not a decimal number: {raw_value!r}") from exc


@dataclass(frozen=True)
class MacroObservation:
    """One normalized macro data point -- a source-agnostic shape that
    `adapters/sources/{ecos,kosis}.py` both converge on."""

    source_id: str
    series_id: str
    period: date
    frequency: MacroFrequency
    value: Decimal
    unit: str
    seasonally_adjusted: bool
    known_at: datetime
    raw_period: str
    raw_value: str
    raw_unit: str


def normalize_macro_observation(
    *,
    source_id: str,
    series_id: str,
    raw_period: str,
    frequency: MacroFrequency,
    raw_value: str,
    raw_unit: str,
    seasonally_adjusted: bool,
    publication_lag_days: int,
) -> MacroObservation:
    """Compose period/unit/value/known_at normalization into one
    `MacroObservation`. Every failure mode below raises a typed,
    fail-closed error -- there is no code path that returns a partially
    normalized or best-guess observation."""
    if not source_id:
        raise ValueError("source_id is required")
    if not series_id:
        raise ValueError("series_id is required")
    period = period_end(raw_period, frequency)
    normalized_unit = normalize_unit(raw_unit)
    value = _parse_value(raw_value) * normalized_unit.scale
    known_at = compute_known_at(period, publication_lag_days=publication_lag_days)
    return MacroObservation(
        source_id=source_id,
        series_id=series_id,
        period=period,
        frequency=frequency,
        value=value,
        unit=normalized_unit.unit,
        seasonally_adjusted=seasonally_adjusted,
        known_at=known_at,
        raw_period=raw_period,
        raw_value=raw_value,
        raw_unit=raw_unit,
    )
