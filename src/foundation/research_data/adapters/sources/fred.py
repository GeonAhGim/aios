# ratchet-allow: FRED `series/observations` request path / response field
# names are cited from the public API guide (fred.stlouisfed.org/docs/api),
# not re-verified against a live call in this session -- every parsing step
# below fails closed (FredParseError/FredApiError) instead of guessing past
# a mismatch.
"""RD-13 -- adapters/sources/fred.py: FRED (Federal Reserve Economic Data,
St. Louis Fed) macro-data collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.2
`adapters/sources/fred.py`, sec.9 RD-13 ("거시 시계열").

FRED's `series/observations` endpoint takes a query string
(`series_id=...&api_key=...&file_type=json[&observation_start=...
&observation_end=...]`) and returns `{"observations": [{"date": "YYYY-MM-DD",
"value": "...", ...}, ...]}` on success, or `{"error_code": N,
"error_message": "..."}` on failure (RD-1 RESEARCH_DATA_SOURCE_EVAL.md
sec.5, citing fred.stlouisfed.org/docs/api/fred/errors.html).

Unlike ECOS/KOSIS, FRED always reports `date` as a full ISO calendar date
(`YYYY-MM-DD`, the first day of the observation's period) regardless of the
series' own frequency -- `domain/macro_series.period_end` instead expects a
raw period code shaped by the caller's declared `MacroFrequency` (e.g.
`YYYYMM` for MONTHLY). This module's `_iso_date_to_raw_period` performs that
one deterministic reshaping (a documented FRED wire convention, not a guess)
before handing the string to `normalize_macro_observation`.

RD-1 also records that some FRED series carry a third-party copyright
holder's `source note` (e.g. a private data provider), in which case that
series must be redistributed as `link_only`/`store_excerpt`, never
`store_full` (RESEARCH_DATA_SOURCE_EVAL.md sec.5). This module surfaces the
series' `notes` field to the caller (`FredSeriesMeta.has_third_party_notice`)
rather than deciding the redistribution policy itself -- that decision
belongs to `domain/redistribution.py` (RD-3), not to a source adapter.

This module only builds the request and parses the response envelope --
normalization of frequency/unit/publication-lag is fully delegated to
`domain/macro_series.py` (this file may do I/O, that one may not, L0-2).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from typing import Any, Protocol

from src.foundation.research_data.domain.macro_series import (
    MacroFrequency,
    MacroObservation,
    normalize_macro_observation,
)
from src.foundation.research_data.domain.source_eval_parse import EXPECTED_ADMISSION

__all__ = [
    "FRED_SOURCE_ID",
    "FredApiError",
    "FredParseError",
    "FredHttpClient",
    "FredSeriesRequest",
    "FredSeriesMeta",
    "evaluate_fred_series_notice",
    "parse_fred_observations",
    "fetch_fred_series",
]

FRED_SOURCE_ID = "FRED"

_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# `.` is FRED's own documented sentinel for a missing observation value
# (fred.stlouisfed.org/docs/api/fred/series_observations.html) -- rows
# carrying it are dropped rather than passed to `_parse_value`, which would
# otherwise raise a parse error for what FRED itself marks as "no data".
_MISSING_VALUE_SENTINEL = "."

_THIRD_PARTY_NOTICE_MARKERS: tuple[str, ...] = (
    "copyright",
    "©",
    "all rights reserved",
)


class FredApiError(RuntimeError):
    """FRED returned its `error_code`/`error_message` envelope, or the
    request was never sent because no API key was configured (fail-closed,
    not silent)."""


class FredParseError(ValueError):
    """An observation was missing an expected field, its `date` was not a
    valid ISO calendar date, or the response had neither an `observations`
    list nor an `error_code` -- an unrecognized shape is treated as a parse
    failure, never guessed past."""


class FredHttpClient(Protocol):
    """Injected transport -- this module never constructs its own HTTP
    session, so a unit test can supply a `Mock`/fixture double without any
    network access."""

    async def get_json(self, url: str, *, params: Mapping[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FredSeriesRequest:
    series_id: str
    frequency: MacroFrequency
    observation_start: str = ""
    observation_end: str = ""
    seasonally_adjusted: bool = False


@dataclass(frozen=True)
class FredSeriesMeta:
    """Third-party-notice flag for the series `notes` field (RD-1 sec.5) --
    a source adapter only surfaces this fact; RD-3's `domain/redistribution.py`
    decides what redistribution policy it implies."""

    series_id: str
    has_third_party_notice: bool


def _iso_date_to_raw_period(iso_date: str, frequency: MacroFrequency) -> str:
    try:
        year, month, day = (int(part) for part in iso_date.split("-"))
        parsed = date_(year, month, day)
    except ValueError as exc:
        raise FredParseError(
            f"FRED observation date is not a valid ISO date: {iso_date!r}"
        ) from exc
    if frequency is MacroFrequency.ANNUAL:
        return f"{parsed.year:04d}"
    if frequency is MacroFrequency.SEMIANNUAL:
        half = 1 if parsed.month <= 6 else 2
        return f"{parsed.year:04d}S{half}"
    if frequency is MacroFrequency.QUARTERLY:
        quarter = (parsed.month - 1) // 3 + 1
        return f"{parsed.year:04d}Q{quarter}"
    if frequency is MacroFrequency.MONTHLY:
        return f"{parsed.year:04d}{parsed.month:02d}"
    if frequency is MacroFrequency.DAILY:
        return f"{parsed.year:04d}{parsed.month:02d}{parsed.day:02d}"
    raise FredParseError(f"unsupported frequency for FRED: {frequency!r}")


def evaluate_fred_series_notice(series_id: str, notes: str) -> FredSeriesMeta:
    """Flag whether a FRED series' `notes` field (from its `series` metadata
    endpoint, fetched separately by the caller) names a third-party
    copyright holder -- RD-1 sec.5: such series must be redistributed as
    `link_only`/`store_excerpt`, never `store_full`. This function only
    detects the marker; `domain/redistribution.py` (RD-3) owns the actual
    redistribution-policy decision."""
    lowered = notes.lower()
    has_notice = any(marker in lowered for marker in _THIRD_PARTY_NOTICE_MARKERS)
    return FredSeriesMeta(series_id=series_id, has_third_party_notice=has_notice)


def _parse_observation(
    row: dict[str, Any],
    *,
    series_id: str,
    frequency: MacroFrequency,
    seasonally_adjusted: bool,
    publication_lag_days: int,
) -> MacroObservation | None:
    try:
        raw_date = str(row["date"])
        raw_value = str(row["value"])
    except KeyError as exc:
        raise FredParseError(f"FRED observation missing expected field {exc}") from exc
    if raw_value.strip() == _MISSING_VALUE_SENTINEL:
        return None
    raw_period = _iso_date_to_raw_period(raw_date, frequency)
    return normalize_macro_observation(
        source_id=FRED_SOURCE_ID,
        series_id=series_id,
        raw_period=raw_period,
        frequency=frequency,
        raw_value=raw_value,
        raw_unit="pt",
        seasonally_adjusted=seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )


def parse_fred_observations(
    payload: dict[str, Any],
    *,
    series_id: str,
    frequency: MacroFrequency,
    seasonally_adjusted: bool = False,
    publication_lag_days: int,
) -> list[MacroObservation]:
    """Parse one FRED `series/observations` JSON payload into normalized
    observations. Raises `FredApiError` if FRED reported an `error_code`
    envelope, `FredParseError` for any other unrecognized/incomplete shape.

    FRED's own units metadata (`units`/`units_short`, e.g. `"Percent"` or
    `"Bil. of $"`) is not fetched here -- resolving it would require a
    second `series` metadata call this leaf does not make, so every
    observation is normalized as a bare index value (`raw_unit="pt"`, which
    `domain/macro_series.normalize_unit` resolves to canonical unit
    `"index"`, scale 1). Callers needing a different unit must resolve it
    themselves and are free to call `domain/macro_series.normalize_unit`
    directly.
    """
    observations = payload.get("observations")
    if observations is None:
        if "error_code" in payload:
            raise FredApiError(
                f"FRED_ERROR:{payload.get('error_code')}:{payload.get('error_message')}"
            )
        raise FredParseError("FRED response has neither observations nor error_code")
    result: list[MacroObservation] = []
    for row in observations:
        observation = _parse_observation(
            row,
            series_id=series_id,
            frequency=frequency,
            seasonally_adjusted=seasonally_adjusted,
            publication_lag_days=publication_lag_days,
        )
        if observation is not None:
            result.append(observation)
    return result


async def fetch_fred_series(
    client: FredHttpClient,
    *,
    api_key: str,
    request: FredSeriesRequest,
    publication_lag_days: int,
) -> list[MacroObservation]:
    """Fetch and normalize one FRED series. `api_key` must be a non-empty,
    user-provisioned key (RD spec sec.10: "국내 소스 API 키... 키 없으면
    어댑터는 명시적으로 비활성(무음 실패 금지)"; FRED is a Layer A source
    under the same key-provisioning posture) -- an empty key raises instead
    of silently returning an empty list.

    RD-1's `RESEARCH_DATA_SOURCE_EVAL.md` records FRED's admission as
    `allow` -- `check_research_data_source_eval.py`/`source_eval_gate.py`
    enforce that this can never silently flip to `deny` without the
    document itself changing. This call refuses to reach the network if
    that admission ever does flip, rather than caveat it after the fact.
    """
    if EXPECTED_ADMISSION["FRED"] != "allow":
        raise FredApiError(
            "FRED is denied by RD-1 RESEARCH_DATA_SOURCE_EVAL.md -- "
            "ingestion stays disabled until that admission flips back to allow"
        )
    if not api_key:
        raise FredApiError(
            "FRED API key not configured -- adapter stays disabled, not silently skipped"
        )
    params = {
        "series_id": request.series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if request.observation_start:
        params["observation_start"] = request.observation_start
    if request.observation_end:
        params["observation_end"] = request.observation_end
    payload = await client.get_json(_FRED_BASE_URL, params=params)
    return parse_fred_observations(
        payload,
        series_id=request.series_id,
        frequency=request.frequency,
        seasonally_adjusted=request.seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )
