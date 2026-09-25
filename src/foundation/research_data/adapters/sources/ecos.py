# ratchet-allow: ECOS StatisticSearch request path / response field names are
# cited from the public Open API guide (ecos.bok.or.kr), not re-verified
# against a live call in this session -- every parsing step below fails
# closed (EcosParseError/EcosApiError) instead of guessing past a mismatch.
"""RD-11 -- adapters/sources/ecos.py: Bank of Korea ECOS (경제통계시스템)
macro-data collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.2
`adapters/sources/{ecos,kosis}.py`, sec.9 RD-11.

ECOS's `StatisticSearch` endpoint takes its request as `/`-joined path
segments (no query string):
`.../StatisticSearch/{auth_key}/{format}/{lang}/{start}/{end}/{stat_code}/
{cycle}/{start_period}/{end_period}/{item_code1}[/{item_code2}[/...]]`,
and returns `{"StatisticSearch": {"list_total_count": N, "row": [...]}}` on
success or `{"RESULT": {"CODE": ..., "MESSAGE": ...}}` on failure (e.g. a
bad auth key or an empty result set).

This module only builds the request and parses the response envelope --
normalization of frequency/unit/publication-lag is fully delegated to
`domain/macro_series.py` (this file may do I/O, that one may not, L0-2).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.foundation.research_data.domain.macro_series import (
    MacroFrequency,
    MacroObservation,
    normalize_macro_observation,
)
from src.foundation.research_data.domain.source_eval_parse import EXPECTED_ADMISSION

__all__ = [
    "ECOS_SOURCE_ID",
    "EcosApiError",
    "EcosParseError",
    "EcosHttpClient",
    "EcosSeriesRequest",
    "parse_ecos_rows",
    "fetch_ecos_series",
]

ECOS_SOURCE_ID = "ECOS"

_ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"

_FREQUENCY_TO_CYCLE_CODE: dict[MacroFrequency, str] = {
    MacroFrequency.ANNUAL: "A",
    MacroFrequency.SEMIANNUAL: "S",
    MacroFrequency.QUARTERLY: "Q",
    MacroFrequency.MONTHLY: "M",
    MacroFrequency.DAILY: "D",
}


class EcosApiError(RuntimeError):
    """ECOS returned its `RESULT` error envelope, or the request was never
    sent because no auth key was configured (fail-closed, not silent)."""


class EcosParseError(ValueError):
    """A `row` entry was missing an expected field, or the response had
    neither a `StatisticSearch` nor a `RESULT` envelope -- an unrecognized
    shape is treated as a parse failure, never guessed past."""


class EcosHttpClient(Protocol):
    """Injected transport -- this module never constructs its own HTTP
    session, so a unit test can supply a `Mock`/fixture double without any
    network access."""

    async def get_json(self, url: str, *, params: Mapping[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class EcosSeriesRequest:
    stat_code: str
    item_code1: str
    frequency: MacroFrequency
    start_period: str
    end_period: str
    item_code2: str = ""
    item_code3: str = ""
    item_code4: str = ""
    seasonally_adjusted: bool = False


def _request_url(
    auth_key: str, request: EcosSeriesRequest, *, start_count: int, end_count: int
) -> str:
    cycle = _FREQUENCY_TO_CYCLE_CODE[request.frequency]
    segments = [
        _ECOS_BASE_URL,
        auth_key,
        "json",
        "kr",
        str(start_count),
        str(end_count),
        request.stat_code,
        cycle,
        request.start_period,
        request.end_period,
        request.item_code1,
    ]
    for extra in (request.item_code2, request.item_code3, request.item_code4):
        if extra:
            segments.append(extra)
    return "/".join(segments)


def _parse_row(
    row: dict[str, Any],
    *,
    frequency: MacroFrequency,
    seasonally_adjusted: bool,
    publication_lag_days: int,
) -> MacroObservation:
    try:
        stat_code = str(row["STAT_CODE"])
        item_code1 = str(row["ITEM_CODE1"])
        raw_period = str(row["TIME"])
        raw_value = str(row["DATA_VALUE"])
        raw_unit = str(row["UNIT_NAME"])
    except KeyError as exc:
        raise EcosParseError(f"ECOS row missing expected field {exc}") from exc
    series_id = f"{stat_code}:{item_code1}"
    return normalize_macro_observation(
        source_id=ECOS_SOURCE_ID,
        series_id=series_id,
        raw_period=raw_period,
        frequency=frequency,
        raw_value=raw_value,
        raw_unit=raw_unit,
        seasonally_adjusted=seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )


def parse_ecos_rows(
    payload: dict[str, Any],
    *,
    frequency: MacroFrequency,
    seasonally_adjusted: bool = False,
    publication_lag_days: int,
) -> list[MacroObservation]:
    """Parse one ECOS `StatisticSearch` JSON payload into normalized
    observations. Raises `EcosApiError` if ECOS reported a `RESULT` error
    envelope, `EcosParseError` for any other unrecognized/incomplete shape."""
    container = payload.get("StatisticSearch")
    if container is None:
        result = payload.get("RESULT")
        if isinstance(result, dict):
            raise EcosApiError(f"ECOS_ERROR:{result.get('CODE')}:{result.get('MESSAGE')}")
        raise EcosParseError("ECOS response has neither StatisticSearch nor RESULT envelope")
    rows = container.get("row", [])
    return [
        _parse_row(
            row,
            frequency=frequency,
            seasonally_adjusted=seasonally_adjusted,
            publication_lag_days=publication_lag_days,
        )
        for row in rows
    ]


async def fetch_ecos_series(
    client: EcosHttpClient,
    *,
    auth_key: str,
    request: EcosSeriesRequest,
    publication_lag_days: int,
    start_count: int = 1,
    end_count: int = 10_000,
) -> list[MacroObservation]:
    """Fetch and normalize one ECOS series. `auth_key` must be a non-empty,
    user-provisioned key (RD spec sec.10: "국내 소스 API 키... 키 없으면
    어댑터는 명시적으로 비활성(무음 실패 금지)") -- an empty key raises
    instead of silently returning an empty list.

    RD-1's `RESEARCH_DATA_SOURCE_EVAL.md` records ECOS's rate limit as
    unconfirmed and its admission as `deny` ("반입 금지") --
    `check_research_data_source_eval.py`/`source_eval_gate.py` enforce that
    an unconfirmed rate limit can never flip to `allow` without the doc
    itself changing. This call refuses to reach the network at all until
    that admission changes, rather than caveat it after the fact.
    """
    if EXPECTED_ADMISSION["ECOS"] != "allow":
        raise EcosApiError(
            "ECOS is denied by RD-1 RESEARCH_DATA_SOURCE_EVAL.md "
            "(rate limit unconfirmed) -- ingestion stays disabled until that "
            "admission flips to allow"
        )
    if not auth_key:
        raise EcosApiError(
            "ECOS auth key not configured -- adapter stays disabled, not silently skipped"
        )
    url = _request_url(auth_key, request, start_count=start_count, end_count=end_count)
    payload = await client.get_json(url, params={})
    return parse_ecos_rows(
        payload,
        frequency=request.frequency,
        seasonally_adjusted=request.seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )
