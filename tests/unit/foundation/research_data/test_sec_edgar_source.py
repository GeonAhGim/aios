"""RD-14 -- `adapters/sources/sec_edgar.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-14
DoD ("10-K/Q, 8-K normalization").
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.foundation.research_data.adapters.sources.sec_edgar import (
    SEC_EDGAR_SOURCE_ID,
    SecEdgarApiError,
    SecEdgarFilingRequest,
    SecEdgarParseError,
    fetch_sec_edgar_filings,
    parse_sec_edgar_submissions,
)


def _submissions(**overrides: Any) -> dict[str, Any]:
    recent = {
        "form": ["10-K", "8-K", "4", "10-Q"],
        "accessionNumber": [
            "0000320193-24-000010",
            "0000320193-24-000011",
            "0000320193-24-000012",
            "0000320193-24-000013",
        ],
        "filingDate": ["2024-02-15", "2024-03-01", "2024-03-05", "2024-05-10"],
        "acceptanceDateTime": [
            "2024-02-15T18:30:00.000Z",
            "2024-03-01T20:15:00.000Z",
            "2024-03-05T09:00:00.000Z",
            "2024-05-10T16:45:00.000Z",
        ],
        "primaryDocument": ["aapl-20240215.htm", "aapl-20240301.htm", "", "aapl-20240510.htm"],
    }
    recent.update(overrides)
    return {"cik": "0000320193", "filings": {"recent": recent}}


def test_parse_sec_edgar_submissions_keeps_only_relevant_forms() -> None:
    items = parse_sec_edgar_submissions(_submissions(), cik="320193")
    assert [item.title.split(" ")[0] for item in items] == ["10-K", "8-K", "10-Q"]


def test_parse_sec_edgar_submissions_normalizes_dates_and_source_id() -> None:
    items = parse_sec_edgar_submissions(_submissions(), cik="320193")
    ten_k = items[0]
    assert ten_k.source_id == SEC_EDGAR_SOURCE_ID
    assert ten_k.kind == "filing"
    assert ten_k.published_at == datetime(2024, 2, 15, tzinfo=timezone.utc)
    assert ten_k.known_at == datetime(2024, 2, 15, 18, 30, tzinfo=timezone.utc)
    assert ten_k.body_ref is None
    assert ten_k.language == "en"


def test_parse_sec_edgar_submissions_builds_archive_url_with_primary_document() -> None:
    items = parse_sec_edgar_submissions(_submissions(), cik="320193")
    ten_k = items[0]
    assert ten_k.url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000010/aapl-20240215.htm"
    )


def test_parse_sec_edgar_submissions_falls_back_to_index_url_without_primary_document() -> None:
    items = parse_sec_edgar_submissions(
        _submissions(
            form=["8-K"],
            accessionNumber=["0000320193-24-000012"],
            filingDate=["2024-03-05"],
            acceptanceDateTime=["2024-03-05T09:00:00.000Z"],
            primaryDocument=[""],
        ),
        cik="320193",
    )
    assert items[0].url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000012/"


def test_parse_sec_edgar_submissions_instruments_include_ticker_when_given() -> None:
    items = parse_sec_edgar_submissions(_submissions(), cik="320193", ticker="aapl")
    assert items[0].instruments == ("AAPL:KIS_US", "CIK0000320193")


def test_parse_sec_edgar_submissions_instruments_fall_back_to_raw_cik() -> None:
    items = parse_sec_edgar_submissions(_submissions(), cik="320193")
    assert items[0].instruments == ("CIK0000320193",)


def test_parse_sec_edgar_submissions_is_deterministic_across_calls() -> None:
    first = parse_sec_edgar_submissions(_submissions(), cik="320193")
    second = parse_sec_edgar_submissions(_submissions(), cik="320193")
    assert [item.item_id for item in first] == [item.item_id for item in second]
    assert [item.hash for item in first] == [item.hash for item in second]


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_sec_edgar_submissions_raises_on_missing_filings_object() -> None:
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions({"cik": "320193"}, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_missing_recent_object() -> None:
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions({"filings": {}}, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_missing_column() -> None:
    payload = _submissions()
    del payload["filings"]["recent"]["primaryDocument"]
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions(payload, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_mismatched_column_lengths() -> None:
    payload = _submissions()
    payload["filings"]["recent"]["form"] = ["10-K"]
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions(payload, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_invalid_filing_date() -> None:
    payload = _submissions(filingDate=["not-a-date", "2024-03-01", "2024-03-05", "2024-05-10"])
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions(payload, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_invalid_acceptance_datetime() -> None:
    payload = _submissions(
        acceptanceDateTime=["not-a-datetime", "2024-03-01T20:15:00.000Z", "x", "y"]
    )
    with pytest.raises(SecEdgarParseError):
        parse_sec_edgar_submissions(payload, cik="320193")


def test_parse_sec_edgar_submissions_raises_on_malformed_cik() -> None:
    with pytest.raises(SecEdgarApiError):
        parse_sec_edgar_submissions(_submissions(), cik="not-a-cik")


async def test_fetch_sec_edgar_filings_rejects_empty_user_agent() -> None:
    client = AsyncMock()
    request = SecEdgarFilingRequest(cik="320193")
    with pytest.raises(SecEdgarApiError):
        await fetch_sec_edgar_filings(client, user_agent="", request=request)
    client.get_json.assert_not_called()


# --- red-gate reproduction: RD-1 admission flip must block, not warn -------


async def test_fetch_sec_edgar_filings_denied_when_admission_flips_red() -> None:
    client = AsyncMock()
    request = SecEdgarFilingRequest(cik="320193")
    admission_override = {"SEC EDGAR": "deny"}
    with patch(
        "src.foundation.research_data.adapters.sources.sec_edgar.EXPECTED_ADMISSION",
        admission_override,
    ):
        with pytest.raises(SecEdgarApiError):
            await fetch_sec_edgar_filings(
                client, user_agent="AIOS research@example.com", request=request
            )
    client.get_json.assert_not_called()


# --- failure injection: transport raises ------------------------------------


async def test_fetch_sec_edgar_filings_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get_json.side_effect = ConnectionError("SEC EDGAR unreachable")
    request = SecEdgarFilingRequest(cik="320193")
    with pytest.raises(ConnectionError):
        await fetch_sec_edgar_filings(
            client, user_agent="AIOS research@example.com", request=request
        )


async def test_fetch_sec_edgar_filings_builds_url_and_headers() -> None:
    client = AsyncMock()
    client.get_json.return_value = _submissions()
    request = SecEdgarFilingRequest(cik="320193", ticker="AAPL")
    items = await fetch_sec_edgar_filings(
        client, user_agent="AIOS research@example.com", request=request
    )
    assert len(items) == 3
    call = client.get_json.call_args
    assert call.args[0] == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert call.kwargs["headers"] == {"User-Agent": "AIOS research@example.com"}


# --- numeric performance assertion (ADR-2026-09-09-C budget: pure CPU-bound
# normalization of a realistic batch must clear a fixed throughput floor) ---


def test_parse_sec_edgar_submissions_throughput_floor() -> None:
    size = 1_000
    payload = _submissions(
        form=["10-K", "10-Q", "8-K"] * size,
        accessionNumber=[f"0000320193-24-{i:06d}" for i in range(3 * size)],
        filingDate=["2024-02-15"] * (3 * size),
        acceptanceDateTime=["2024-02-15T18:30:00.000Z"] * (3 * size),
        primaryDocument=["aapl-20240215.htm"] * (3 * size),
    )
    started = time.perf_counter()
    items = parse_sec_edgar_submissions(payload, cik="320193")
    elapsed = time.perf_counter() - started
    assert len(items) == 3 * size
    throughput = len(items) / elapsed
    assert throughput > 2_000, f"parse_sec_edgar_submissions throughput too low: {throughput:.0f}/s"
