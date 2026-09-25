# ratchet-allow: KOSIS statisticsParameterData request/response field names
# are cited from the public Open API guide (kosis.kr/openapi), not
# re-verified against a live call in this session -- every parsing step
# below fails closed (KosisParseError/KosisApiError) instead of guessing
# past a mismatch.
"""RD-11 -- adapters/sources/kosis.py: KOSIS (국가통계포털) macro-data
collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.2
`adapters/sources/{ecos,kosis}.py`, sec.9 RD-11.

KOSIS's `statisticsParameterData` endpoint takes a query string
(`method=getList&apiKey=...&orgId=...&tblId=...&itmId=...&objL1=...&
prdSe=...&startPrdDe=...&endPrdDe=...&format=json&jsonVD=Y`) and returns a
bare JSON array of row objects on success, or a JSON object carrying an
`err`/`errMsg` pair on failure -- unlike ECOS, there is no shared envelope
key to branch on, so the payload's own type (`list` vs `dict`) is what
distinguishes the two.

This module only builds the request and parses the response rows --
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
    "KOSIS_SOURCE_ID",
    "KosisApiError",
    "KosisParseError",
    "KosisHttpClient",
    "KosisSeriesRequest",
    "parse_kosis_rows",
    "fetch_kosis_series",
]

KOSIS_SOURCE_ID = "KOSIS"

_KOSIS_BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

_FREQUENCY_TO_PRD_SE: dict[MacroFrequency, str] = {
    MacroFrequency.ANNUAL: "Y",
    MacroFrequency.SEMIANNUAL: "S",
    MacroFrequency.QUARTERLY: "Q",
    MacroFrequency.MONTHLY: "M",
    MacroFrequency.DAILY: "D",
}
_PRD_SE_TO_FREQUENCY: dict[str, MacroFrequency] = {v: k for k, v in _FREQUENCY_TO_PRD_SE.items()}


class KosisApiError(RuntimeError):
    """KOSIS returned its `err`/`errMsg` error object, or the request was
    never sent because no API key was configured (fail-closed, not
    silent)."""


class KosisParseError(ValueError):
    """A row was missing an expected field, its `PRD_SE` did not match any
    known frequency code, or the payload was neither a list of rows nor a
    recognizable error object."""


class KosisHttpClient(Protocol):
    """Injected transport -- this module never constructs its own HTTP
    session, so a unit test can supply a `Mock`/fixture double without any
    network access."""

    async def get_json(self, url: str, *, params: Mapping[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class KosisSeriesRequest:
    org_id: str
    tbl_id: str
    itm_id: str
    frequency: MacroFrequency
    start_period: str
    end_period: str
    obj_l1: str = ""
    seasonally_adjusted: bool = False


def _request_params(request: KosisSeriesRequest) -> dict[str, str]:
    try:
        prd_se = _FREQUENCY_TO_PRD_SE[request.frequency]
    except KeyError as exc:
        raise KosisParseError(f"unsupported frequency for KOSIS: {request.frequency!r}") from exc
    params = {
        "method": "getList",
        "format": "json",
        "jsonVD": "Y",
        "orgId": request.org_id,
        "tblId": request.tbl_id,
        "itmId": request.itm_id,
        "prdSe": prd_se,
        "startPrdDe": request.start_period,
        "endPrdDe": request.end_period,
    }
    if request.obj_l1:
        params["objL1"] = request.obj_l1
    return params


def _parse_row(
    row: dict[str, Any], *, seasonally_adjusted: bool, publication_lag_days: int
) -> MacroObservation:
    try:
        tbl_id = str(row["TBL_ID"])
        itm_id = str(row["ITM_ID"])
        prd_se = str(row["PRD_SE"])
        raw_period = str(row["PRD_DE"])
        raw_value = str(row["DT"])
        raw_unit = str(row["UNIT_NM"])
    except KeyError as exc:
        raise KosisParseError(f"KOSIS row missing expected field {exc}") from exc
    frequency = _PRD_SE_TO_FREQUENCY.get(prd_se)
    if frequency is None:
        raise KosisParseError(f"KOSIS row has unrecognized PRD_SE: {prd_se!r}")
    obj_l1 = row.get("C1")
    series_id = f"{tbl_id}:{itm_id}:{obj_l1}" if obj_l1 else f"{tbl_id}:{itm_id}"
    return normalize_macro_observation(
        source_id=KOSIS_SOURCE_ID,
        series_id=series_id,
        raw_period=raw_period,
        frequency=frequency,
        raw_value=raw_value,
        raw_unit=raw_unit,
        seasonally_adjusted=seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )


def parse_kosis_rows(
    payload: Any,
    *,
    seasonally_adjusted: bool = False,
    publication_lag_days: int,
) -> list[MacroObservation]:
    """Parse one KOSIS `statisticsParameterData` JSON payload into
    normalized observations. Raises `KosisApiError` if KOSIS reported an
    `err`/`errMsg` object, `KosisParseError` for any other
    unrecognized/incomplete shape."""
    if isinstance(payload, dict):
        if "err" in payload:
            raise KosisApiError(f"KOSIS_ERROR:{payload.get('err')}:{payload.get('errMsg')}")
        raise KosisParseError("KOSIS response is a JSON object without an err/errMsg pair")
    if not isinstance(payload, list):
        raise KosisParseError(
            f"KOSIS response is neither a list nor an error object: {type(payload)!r}"
        )
    return [
        _parse_row(
            row,
            seasonally_adjusted=seasonally_adjusted,
            publication_lag_days=publication_lag_days,
        )
        for row in payload
    ]


async def fetch_kosis_series(
    client: KosisHttpClient,
    *,
    api_key: str,
    request: KosisSeriesRequest,
    publication_lag_days: int,
) -> list[MacroObservation]:
    """Fetch and normalize one KOSIS series. `api_key` must be a non-empty,
    user-provisioned key (RD spec sec.10: "국내 소스 API 키... 키 없으면
    어댑터는 명시적으로 비활성(무음 실패 금지)") -- an empty key raises
    instead of silently returning an empty list.

    RD-1's `RESEARCH_DATA_SOURCE_EVAL.md` records KOSIS's rate limit as
    unconfirmed and its admission as `deny` ("반입 금지") --
    `check_research_data_source_eval.py`/`source_eval_gate.py` enforce that
    an unconfirmed rate limit can never flip to `allow` without the doc
    itself changing. This call refuses to reach the network at all until
    that admission changes, rather than caveat it after the fact.
    """
    if EXPECTED_ADMISSION["KOSIS"] != "allow":
        raise KosisApiError(
            "KOSIS is denied by RD-1 RESEARCH_DATA_SOURCE_EVAL.md "
            "(rate limit unconfirmed) -- ingestion stays disabled until that "
            "admission flips to allow"
        )
    if not api_key:
        raise KosisApiError(
            "KOSIS API key not configured -- adapter stays disabled, not silently skipped"
        )
    params = _request_params(request)
    params["apiKey"] = api_key
    payload = await client.get_json(_KOSIS_BASE_URL, params=params)
    return parse_kosis_rows(
        payload,
        seasonally_adjusted=request.seasonally_adjusted,
        publication_lag_days=publication_lag_days,
    )
