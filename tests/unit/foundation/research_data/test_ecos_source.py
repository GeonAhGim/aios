"""RD-11 -- `adapters/sources/ecos.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-11
DoD ("빈도·단위·발표지연 정규화").
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.foundation.research_data.adapters.sources.ecos import (
    ECOS_SOURCE_ID,
    EcosApiError,
    EcosParseError,
    EcosSeriesRequest,
    fetch_ecos_series,
    parse_ecos_rows,
)
from src.foundation.research_data.domain.macro_series import MacroFrequency


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "STAT_CODE": "722Y001",
        "ITEM_CODE1": "0101000",
        "TIME": "202401",
        "DATA_VALUE": "3.5",
        "UNIT_NAME": "%",
    }
    row.update(overrides)
    return row


def test_parse_ecos_rows_normalizes_successfully() -> None:
    payload = {"StatisticSearch": {"list_total_count": 1, "row": [_row()]}}
    observations = parse_ecos_rows(
        payload, frequency=MacroFrequency.MONTHLY, publication_lag_days=20
    )
    assert len(observations) == 1
    observation = observations[0]
    assert observation.source_id == ECOS_SOURCE_ID
    assert observation.series_id == "722Y001:0101000"
    assert observation.period == date(2024, 1, 31)
    assert observation.value == Decimal("3.5")
    assert observation.unit == "percent"
    assert observation.known_at == datetime(2024, 2, 20, tzinfo=timezone.utc)


def test_parse_ecos_rows_empty_row_list() -> None:
    payload = {"StatisticSearch": {"list_total_count": 0, "row": []}}
    assert parse_ecos_rows(payload, frequency=MacroFrequency.MONTHLY, publication_lag_days=0) == []


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_ecos_rows_raises_api_error_on_result_envelope() -> None:
    payload = {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다"}}
    with pytest.raises(EcosApiError):
        parse_ecos_rows(payload, frequency=MacroFrequency.MONTHLY, publication_lag_days=0)


def test_parse_ecos_rows_raises_parse_error_on_unrecognized_envelope() -> None:
    with pytest.raises(EcosParseError):
        parse_ecos_rows(
            {"unexpected": True}, frequency=MacroFrequency.MONTHLY, publication_lag_days=0
        )


def test_parse_ecos_rows_raises_parse_error_on_missing_field() -> None:
    incomplete_row = _row()
    del incomplete_row["DATA_VALUE"]
    payload = {"StatisticSearch": {"row": [incomplete_row]}}
    with pytest.raises(EcosParseError):
        parse_ecos_rows(payload, frequency=MacroFrequency.MONTHLY, publication_lag_days=0)


async def test_fetch_ecos_series_denied_by_rd1_source_eval() -> None:
    """RD-1's RESEARCH_DATA_SOURCE_EVAL.md records ECOS's rate limit as
    unconfirmed, so EXPECTED_ADMISSION["ECOS"] == "deny" -- the adapter must
    refuse before ever touching the network, with today's real (unpatched)
    admission state."""
    client = AsyncMock()
    request = EcosSeriesRequest(
        stat_code="722Y001",
        item_code1="0101000",
        frequency=MacroFrequency.MONTHLY,
        start_period="202401",
        end_period="202401",
    )
    with pytest.raises(EcosApiError):
        await fetch_ecos_series(client, auth_key="key", request=request, publication_lag_days=20)
    client.get_json.assert_not_called()


async def test_fetch_ecos_series_rejects_empty_auth_key() -> None:
    client = AsyncMock()
    request = EcosSeriesRequest(
        stat_code="722Y001",
        item_code1="0101000",
        frequency=MacroFrequency.MONTHLY,
        start_period="202401",
        end_period="202401",
    )
    admission_override = {"ECOS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.ecos.EXPECTED_ADMISSION", admission_override
    ):
        with pytest.raises(EcosApiError):
            await fetch_ecos_series(client, auth_key="", request=request, publication_lag_days=20)
    client.get_json.assert_not_called()


# --- failure injection: transport raises -----------------------------------


async def test_fetch_ecos_series_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get_json.side_effect = ConnectionError("ECOS unreachable")
    request = EcosSeriesRequest(
        stat_code="722Y001",
        item_code1="0101000",
        frequency=MacroFrequency.MONTHLY,
        start_period="202401",
        end_period="202401",
    )
    admission_override = {"ECOS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.ecos.EXPECTED_ADMISSION", admission_override
    ):
        with pytest.raises(ConnectionError):
            await fetch_ecos_series(
                client, auth_key="key", request=request, publication_lag_days=20
            )


async def test_fetch_ecos_series_builds_path_segments_and_normalizes() -> None:
    client = AsyncMock()
    client.get_json.return_value = {"StatisticSearch": {"row": [_row()]}}
    request = EcosSeriesRequest(
        stat_code="722Y001",
        item_code1="0101000",
        frequency=MacroFrequency.MONTHLY,
        start_period="202401",
        end_period="202401",
    )
    admission_override = {"ECOS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.ecos.EXPECTED_ADMISSION", admission_override
    ):
        observations = await fetch_ecos_series(
            client, auth_key="mykey", request=request, publication_lag_days=20
        )
    assert len(observations) == 1
    called_url = client.get_json.call_args.args[0]
    assert called_url.startswith("https://ecos.bok.or.kr/api/StatisticSearch/mykey/json/kr/1/10000/")
    assert called_url.endswith("722Y001/M/202401/202401/0101000")
