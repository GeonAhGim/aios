"""RD-13 -- `adapters/sources/fred.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-13
DoD ("거시 시계열").
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.foundation.research_data.adapters.sources.fred import (
    FRED_SOURCE_ID,
    FredApiError,
    FredParseError,
    FredSeriesRequest,
    evaluate_fred_series_notice,
    fetch_fred_series,
    parse_fred_observations,
)
from src.foundation.research_data.domain.macro_series import MacroFrequency


def _observation(**overrides: Any) -> dict[str, Any]:
    row = {
        "realtime_start": "2024-02-20",
        "realtime_end": "2024-02-20",
        "date": "2024-01-01",
        "value": "3.5",
    }
    row.update(overrides)
    return row


def test_parse_fred_observations_normalizes_successfully() -> None:
    payload = {"observations": [_observation()]}
    observations = parse_fred_observations(
        payload,
        series_id="UNRATE",
        frequency=MacroFrequency.MONTHLY,
        publication_lag_days=20,
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.source_id == FRED_SOURCE_ID
    assert observation.series_id == "UNRATE"
    assert observation.period == date(2024, 1, 31)
    assert observation.value == Decimal("3.5")
    assert observation.unit == "index"
    assert observation.known_at == datetime(2024, 2, 20, tzinfo=timezone.utc)


def test_parse_fred_observations_drops_missing_value_sentinel() -> None:
    payload = {"observations": [_observation(value=".")]}
    observations = parse_fred_observations(
        payload, series_id="UNRATE", frequency=MacroFrequency.MONTHLY, publication_lag_days=0
    )
    assert observations == []


def test_parse_fred_observations_empty_list() -> None:
    payload = {"observations": []}
    assert (
        parse_fred_observations(
            payload, series_id="UNRATE", frequency=MacroFrequency.MONTHLY, publication_lag_days=0
        )
        == []
    )


@pytest.mark.parametrize(
    ("frequency", "iso_date", "expected_period"),
    [
        (MacroFrequency.ANNUAL, "2024-01-01", date(2024, 12, 31)),
        (MacroFrequency.QUARTERLY, "2024-04-01", date(2024, 6, 30)),
        (MacroFrequency.DAILY, "2024-03-15", date(2024, 3, 15)),
    ],
)
def test_parse_fred_observations_reshapes_iso_date_per_frequency(
    frequency: MacroFrequency, iso_date: str, expected_period: date
) -> None:
    payload = {"observations": [_observation(date=iso_date)]}
    observations = parse_fred_observations(
        payload, series_id="GDP", frequency=frequency, publication_lag_days=0
    )
    assert observations[0].period == expected_period


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_fred_observations_raises_api_error_on_error_envelope() -> None:
    payload = {
        "error_code": 400,
        "error_message": "Bad Request. Variable series_id is not a series.",
    }
    with pytest.raises(FredApiError):
        parse_fred_observations(
            payload, series_id="BOGUS", frequency=MacroFrequency.MONTHLY, publication_lag_days=0
        )


def test_parse_fred_observations_raises_parse_error_on_unrecognized_envelope() -> None:
    with pytest.raises(FredParseError):
        parse_fred_observations(
            {"unexpected": True},
            series_id="UNRATE",
            frequency=MacroFrequency.MONTHLY,
            publication_lag_days=0,
        )


def test_parse_fred_observations_raises_parse_error_on_missing_field() -> None:
    incomplete = _observation()
    del incomplete["value"]
    payload = {"observations": [incomplete]}
    with pytest.raises(FredParseError):
        parse_fred_observations(
            payload, series_id="UNRATE", frequency=MacroFrequency.MONTHLY, publication_lag_days=0
        )


def test_parse_fred_observations_raises_parse_error_on_invalid_date() -> None:
    payload = {"observations": [_observation(date="not-a-date")]}
    with pytest.raises(FredParseError):
        parse_fred_observations(
            payload, series_id="UNRATE", frequency=MacroFrequency.MONTHLY, publication_lag_days=0
        )


async def test_fetch_fred_series_rejects_empty_api_key() -> None:
    client = AsyncMock()
    request = FredSeriesRequest(series_id="UNRATE", frequency=MacroFrequency.MONTHLY)
    with pytest.raises(FredApiError):
        await fetch_fred_series(client, api_key="", request=request, publication_lag_days=20)
    client.get_json.assert_not_called()


async def test_fetch_fred_series_denied_when_admission_flips() -> None:
    client = AsyncMock()
    request = FredSeriesRequest(series_id="UNRATE", frequency=MacroFrequency.MONTHLY)
    admission_override = {"FRED": "deny"}
    with patch(
        "src.foundation.research_data.adapters.sources.fred.EXPECTED_ADMISSION", admission_override
    ):
        with pytest.raises(FredApiError):
            await fetch_fred_series(
                client, api_key="key", request=request, publication_lag_days=20
            )
    client.get_json.assert_not_called()


# --- failure injection: transport raises -----------------------------------


async def test_fetch_fred_series_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get_json.side_effect = ConnectionError("FRED unreachable")
    request = FredSeriesRequest(series_id="UNRATE", frequency=MacroFrequency.MONTHLY)
    with pytest.raises(ConnectionError):
        await fetch_fred_series(client, api_key="key", request=request, publication_lag_days=20)


async def test_fetch_fred_series_builds_params_and_normalizes() -> None:
    client = AsyncMock()
    client.get_json.return_value = {"observations": [_observation()]}
    request = FredSeriesRequest(
        series_id="UNRATE",
        frequency=MacroFrequency.MONTHLY,
        observation_start="2024-01-01",
        observation_end="2024-01-31",
    )
    observations = await fetch_fred_series(
        client, api_key="mykey", request=request, publication_lag_days=20
    )
    assert len(observations) == 1
    call = client.get_json.call_args
    assert call.args[0] == "https://api.stlouisfed.org/fred/series/observations"
    assert call.kwargs["params"] == {
        "series_id": "UNRATE",
        "api_key": "mykey",
        "file_type": "json",
        "observation_start": "2024-01-01",
        "observation_end": "2024-01-31",
    }


def test_evaluate_fred_series_notice_detects_third_party_copyright() -> None:
    meta = evaluate_fred_series_notice("SOMESERIES", "Copyright, 2024, Some Private Vendor.")
    assert meta.has_third_party_notice is True


def test_evaluate_fred_series_notice_no_marker() -> None:
    meta = evaluate_fred_series_notice("UNRATE", "U.S. Bureau of Labor Statistics")
    assert meta.has_third_party_notice is False
