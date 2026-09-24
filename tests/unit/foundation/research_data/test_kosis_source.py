"""RD-11 -- `adapters/sources/kosis.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-11
DoD ("빈도·단위·발표지연 정규화").
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.foundation.research_data.adapters.sources.kosis import (
    KOSIS_SOURCE_ID,
    KosisApiError,
    KosisParseError,
    KosisSeriesRequest,
    fetch_kosis_series,
    parse_kosis_rows,
)
from src.foundation.research_data.domain.macro_series import MacroFrequency


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "TBL_ID": "DT_1YL20631",
        "ITM_ID": "T10",
        "C1": "00",
        "PRD_SE": "Y",
        "PRD_DE": "2024",
        "DT": "1234",
        "UNIT_NM": "억원",
    }
    row.update(overrides)
    return row


def test_parse_kosis_rows_normalizes_successfully() -> None:
    observations = parse_kosis_rows([_row()], publication_lag_days=90)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.source_id == KOSIS_SOURCE_ID
    assert observation.series_id == "DT_1YL20631:T10:00"
    assert observation.period == date(2024, 12, 31)
    assert observation.value == Decimal("123400000000")
    assert observation.unit == "krw"
    assert observation.known_at == datetime(2025, 3, 31, tzinfo=timezone.utc)


def test_parse_kosis_rows_series_id_without_obj_l1() -> None:
    row = _row()
    del row["C1"]
    observations = parse_kosis_rows([row], publication_lag_days=0)
    assert observations[0].series_id == "DT_1YL20631:T10"


def test_parse_kosis_rows_empty_list() -> None:
    assert parse_kosis_rows([], publication_lag_days=0) == []


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_kosis_rows_raises_api_error_on_err_object() -> None:
    payload = {"err": "10", "errMsg": "인증키가 유효하지 않습니다"}
    with pytest.raises(KosisApiError):
        parse_kosis_rows(payload, publication_lag_days=0)


def test_parse_kosis_rows_raises_parse_error_on_unrecognized_prd_se() -> None:
    with pytest.raises(KosisParseError):
        parse_kosis_rows([_row(PRD_SE="X")], publication_lag_days=0)


def test_parse_kosis_rows_raises_parse_error_on_missing_field() -> None:
    incomplete_row = _row()
    del incomplete_row["DT"]
    with pytest.raises(KosisParseError):
        parse_kosis_rows([incomplete_row], publication_lag_days=0)


def test_parse_kosis_rows_raises_parse_error_on_non_list_non_error_payload() -> None:
    with pytest.raises(KosisParseError):
        parse_kosis_rows({"unexpected": True}, publication_lag_days=0)


async def test_fetch_kosis_series_denied_by_rd1_source_eval() -> None:
    """RD-1's RESEARCH_DATA_SOURCE_EVAL.md records KOSIS's rate limit as
    unconfirmed, so EXPECTED_ADMISSION["KOSIS"] == "deny" -- the adapter
    must refuse before ever touching the network, with today's real
    (unpatched) admission state."""
    client = AsyncMock()
    request = KosisSeriesRequest(
        org_id="301",
        tbl_id="DT_1YL20631",
        itm_id="T10",
        frequency=MacroFrequency.ANNUAL,
        start_period="2024",
        end_period="2024",
    )
    with pytest.raises(KosisApiError):
        await fetch_kosis_series(client, api_key="key", request=request, publication_lag_days=90)
    client.get_json.assert_not_called()


async def test_fetch_kosis_series_rejects_empty_api_key() -> None:
    client = AsyncMock()
    request = KosisSeriesRequest(
        org_id="301",
        tbl_id="DT_1YL20631",
        itm_id="T10",
        frequency=MacroFrequency.ANNUAL,
        start_period="2024",
        end_period="2024",
    )
    admission_override = {"KOSIS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.kosis.EXPECTED_ADMISSION", admission_override
    ):
        with pytest.raises(KosisApiError):
            await fetch_kosis_series(client, api_key="", request=request, publication_lag_days=90)
    client.get_json.assert_not_called()


# --- failure injection: transport raises -----------------------------------


async def test_fetch_kosis_series_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get_json.side_effect = ConnectionError("KOSIS unreachable")
    request = KosisSeriesRequest(
        org_id="301",
        tbl_id="DT_1YL20631",
        itm_id="T10",
        frequency=MacroFrequency.ANNUAL,
        start_period="2024",
        end_period="2024",
    )
    admission_override = {"KOSIS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.kosis.EXPECTED_ADMISSION", admission_override
    ):
        with pytest.raises(ConnectionError):
            await fetch_kosis_series(
                client, api_key="key", request=request, publication_lag_days=90
            )


async def test_fetch_kosis_series_builds_params_and_normalizes() -> None:
    client = AsyncMock()
    client.get_json.return_value = [_row()]
    request = KosisSeriesRequest(
        org_id="301",
        tbl_id="DT_1YL20631",
        itm_id="T10",
        frequency=MacroFrequency.ANNUAL,
        start_period="2024",
        end_period="2024",
    )
    admission_override = {"KOSIS": "allow"}
    with patch(
        "src.foundation.research_data.adapters.sources.kosis.EXPECTED_ADMISSION", admission_override
    ):
        observations = await fetch_kosis_series(
            client, api_key="mykey", request=request, publication_lag_days=90
        )
    assert len(observations) == 1
    _, kwargs = client.get_json.call_args
    params = kwargs["params"]
    assert params["apiKey"] == "mykey"
    assert params["prdSe"] == "Y"
    assert params["tblId"] == "DT_1YL20631"
