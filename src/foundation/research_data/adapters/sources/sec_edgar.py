# ratchet-allow: SEC EDGAR `data.sec.gov/submissions/CIK##########.json`
# request path / response field names are cited from public SEC
# documentation (sec.gov/search-filings/edgar-search-assistance/
# accessing-edgar-data, docs/design/RESEARCH_DATA_SOURCE_EVAL.md sec.6),
# not re-verified against a live call in this session -- every parsing
# step below fails closed (SecEdgarParseError/SecEdgarApiError) instead of
# guessing past a mismatch.
"""RD-14 -- adapters/sources/sec_edgar.py: SEC EDGAR (US Securities and
Exchange Commission electronic filing system) collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.2
`adapters/sources/sec_edgar.py`, sec.9 RD-14 ("10-K/Q, 8-K normalization").

RD-1 RESEARCH_DATA_SOURCE_EVAL.md sec.6 records SEC EDGAR's admission as
`allow`: structured metadata (CIK, accession number, form type, filing
date) is redistributable as `store_full`, but each filing's narrative body
stays `store_excerpt` at most -- no explicit sec.gov rights waiver was
found for registrant-authored filing text (17 U.S.C. sec.105 only covers
works authored by federal employees, not filings a private registrant
submits). This adapter only ever normalizes the `submissions` JSON
envelope (CIK, accession number, form, dates, primary document path) -- it
never fetches or stores filing body text, so `ResearchItem.body_ref` is
always `None` here; a future leaf that fetches the narrative body must
apply `domain/redistribution.py`'s `store_excerpt` cap itself, this module
does not pre-empt that decision.

This module only builds the request and parses the response envelope --
entity linking (RD-5) and redistribution enforcement (RD-3) are both the
caller's job, not this adapter's.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime, timezone
from typing import Any, Protocol

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.source_eval_parse import EXPECTED_ADMISSION

__all__ = [
    "SEC_EDGAR_SOURCE_ID",
    "RELEVANT_FORMS",
    "SecEdgarApiError",
    "SecEdgarParseError",
    "SecEdgarHttpClient",
    "SecEdgarFilingRequest",
    "parse_sec_edgar_submissions",
    "fetch_sec_edgar_filings",
]

SEC_EDGAR_SOURCE_ID = "SEC_EDGAR"

# The literal key `EXPECTED_ADMISSION` uses for this source (source_eval_parse
# .py's `LAYER_A_SOURCE_IDS`/RESEARCH_DATA_SOURCE_EVAL.md section heading) --
# kept distinct from `SEC_EDGAR_SOURCE_ID` (the `ResearchItem.source_id`
# convention shared with the other adapters in this package) on purpose.
_SEC_EDGAR_ADMISSION_KEY = "SEC EDGAR"

_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"

_ARCHIVE_URL_TEMPLATE = "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_no_dash}/"

_CIK_DIGITS = 10

# RD-14 DoD scope is "10-K/Q, 8-K normalization" -- amendments ("/A") are the
# same report type re-filed under a new accession number (RD-1 sec.6 (d):
# "structurally consistent with RD-A2 append, but not re-verified against
# sec.gov in this leaf"), so they normalize the same way as the original form.
RELEVANT_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"})

_ITEM_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://data.sec.gov/submissions")


class SecEdgarApiError(RuntimeError):
    """The SEC EDGAR request was refused before it was ever sent -- denied
    by RD-1 admission, a missing/blank `User-Agent`, or a malformed CIK
    (fail-closed, never silent)."""


class SecEdgarParseError(ValueError):
    """A filing entry was missing an expected field, its dates were not
    parseable, or the response had no recognizable `filings.recent`
    envelope -- an unrecognized shape is treated as a parse failure, never
    guessed past."""


class SecEdgarHttpClient(Protocol):
    """Injected transport -- this module never constructs its own HTTP
    session, so a unit test can supply a `Mock`/fixture double without any
    network access."""

    async def get_json(self, url: str, *, headers: Mapping[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SecEdgarFilingRequest:
    cik: str
    ticker: str = ""


def _normalize_cik(cik: str) -> str:
    digits = cik.strip()
    if not digits or not digits.isdigit() or len(digits) > _CIK_DIGITS:
        raise SecEdgarApiError(f"SEC EDGAR CIK must be 1-10 digits, got {cik!r}")
    return digits.zfill(_CIK_DIGITS)


def _parse_acceptance_datetime(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SecEdgarParseError(
            f"SEC EDGAR acceptanceDateTime is not a valid ISO datetime: {raw!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_filing_date(raw: str) -> datetime:
    try:
        year, month, day = (int(part) for part in str(raw).split("-"))
        parsed = date_(year, month, day)
    except ValueError as exc:
        raise SecEdgarParseError(
            f"SEC EDGAR filingDate is not a valid ISO date: {raw!r}"
        ) from exc
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _build_url(cik10: str, accession: str, primary_document: str) -> str:
    cik_no_zeros = str(int(cik10))
    accession_no_dash = accession.replace("-", "")
    base = _ARCHIVE_URL_TEMPLATE.format(
        cik_no_zeros=cik_no_zeros, accession_no_dash=accession_no_dash
    )
    return f"{base}{primary_document}" if primary_document else base


def _build_instruments(cik10: str, ticker: str) -> tuple[str, ...]:
    raw_cik = f"CIK{cik10}"
    if ticker.strip():
        return (f"{ticker.strip().upper()}:KIS_US", raw_cik)
    return (raw_cik,)


def _parse_filing(
    *,
    cik10: str,
    ticker: str,
    accession: str,
    form: str,
    filing_date: str,
    acceptance_datetime: str,
    primary_document: str,
) -> ResearchItem:
    published_at = _parse_filing_date(filing_date)
    known_at = _parse_acceptance_datetime(acceptance_datetime)
    item_id = uuid.uuid5(_ITEM_ID_NAMESPACE, accession)
    digest = hashlib.sha256(f"{accession}:{form}:{filing_date}".encode()).hexdigest()
    return ResearchItem(
        item_id=item_id,
        source_id=SEC_EDGAR_SOURCE_ID,
        kind="filing",
        published_at=published_at,
        known_at=known_at,
        instruments=_build_instruments(cik10, ticker),
        title=f"{form} ({accession})",
        body_ref=None,
        url=_build_url(cik10, accession, primary_document),
        language="en",
        hash=digest,
        revision_of=None,
    )


def parse_sec_edgar_submissions(
    payload: dict[str, Any], *, cik: str, ticker: str = ""
) -> list[ResearchItem]:
    """Parse one SEC EDGAR `submissions/CIK##########.json` payload into
    normalized `ResearchItem`s, keeping only `RELEVANT_FORMS` (10-K/Q,
    8-K, and their `/A` amendments). Raises `SecEdgarParseError` for any
    unrecognized/incomplete shape, `SecEdgarApiError` for a malformed
    `cik`.
    """
    cik10 = _normalize_cik(cik)
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        raise SecEdgarParseError("SEC EDGAR response missing 'filings' object")
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        raise SecEdgarParseError("SEC EDGAR response missing 'filings.recent' object")
    try:
        forms = recent["form"]
        accession_numbers = recent["accessionNumber"]
        filing_dates = recent["filingDate"]
        acceptance_datetimes = recent["acceptanceDateTime"]
        primary_documents = recent["primaryDocument"]
    except KeyError as exc:
        raise SecEdgarParseError(
            f"SEC EDGAR 'filings.recent' missing expected column {exc}"
        ) from exc
    columns = (forms, accession_numbers, filing_dates, acceptance_datetimes, primary_documents)
    if len({len(column) for column in columns}) != 1:
        raise SecEdgarParseError("SEC EDGAR 'filings.recent' columns have mismatched lengths")

    result: list[ResearchItem] = []
    for form, accession, filing_date, acceptance_datetime, primary_document in zip(
        *columns, strict=True
    ):
        if form not in RELEVANT_FORMS:
            continue
        result.append(
            _parse_filing(
                cik10=cik10,
                ticker=ticker,
                accession=accession,
                form=form,
                filing_date=filing_date,
                acceptance_datetime=acceptance_datetime,
                primary_document=primary_document,
            )
        )
    return result


async def fetch_sec_edgar_filings(
    client: SecEdgarHttpClient,
    *,
    user_agent: str,
    request: SecEdgarFilingRequest,
) -> list[ResearchItem]:
    """Fetch and normalize one CIK's 10-K/Q and 8-K filings.

    `user_agent` must be a non-empty string identifying the requester plus
    a contact email (RD-1 RESEARCH_DATA_SOURCE_EVAL.md sec.6 (c): "Please
    declare your user agent in request headers") -- a blank value raises
    instead of silently sending an anonymous request.

    RD-1 records SEC EDGAR's admission as `allow` -- `check_research_data
    _source_eval.py`/`source_eval_gate.py` enforce that this can never
    silently flip to `deny` without the document itself changing. This
    call refuses to reach the network if that admission ever does flip,
    rather than caveat it after the fact.
    """
    if EXPECTED_ADMISSION[_SEC_EDGAR_ADMISSION_KEY] != "allow":
        raise SecEdgarApiError(
            "SEC EDGAR is denied by RD-1 RESEARCH_DATA_SOURCE_EVAL.md -- "
            "ingestion stays disabled until that admission flips back to allow"
        )
    if not user_agent.strip():
        raise SecEdgarApiError(
            "SEC EDGAR requires a User-Agent identifying the requester and a "
            "contact email -- adapter stays disabled, not silently sent anonymously"
        )
    cik10 = _normalize_cik(request.cik)
    url = _SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10)
    payload = await client.get_json(url, headers={"User-Agent": user_agent})
    return parse_sec_edgar_submissions(payload, cik=request.cik, ticker=request.ticker)
