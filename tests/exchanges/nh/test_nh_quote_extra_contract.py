"""NH Adapter 계약 테스트 (generated) -- Task-7008(BR-20a).

Contract tests for the 9 not-started /krstock/quote/* market-data
endpoints (afterHoursCurrent/afterHoursExpected/currentAfterHoursDaily/
currentAfterHoursExecution/currentExecution/currentInvestor/etfComponents/
etfCurrent/period). Split out of test_nh_contract_generated.py (task-6695)
to keep both files under the §7 line-cap warn threshold. Uses fixtures
derived from openapi.json schemas; does NOT hit real API.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.nh.adapter import NHAdapter
from src.exchanges.nh.quote_extra_dto import PeriodQuoteRequest


@pytest.fixture
def nh_adapter() -> NHAdapter:
    """Minimal NHAdapter for testing."""
    return NHAdapter(
        app_key="test_key",
        app_secret="test_secret",
        act_no="",  # Optional for market-data-only tests
    )




@pytest.mark.asyncio
async def test_get_after_hours_current_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {
                "iem_cd": "005930",
                "iem_nm": "삼성전자",
                "ovtm_untp_prpr": "51000",
                "ovtm_untp_vol": "1234",
                "ovtm_prdy_vrss": "500",
                "ovtm_prdy_ctrt": "1.2",
                "prdy_vrss_sign": "2",
            }
        }
        result = await nh_adapter.get_after_hours_current("005930")

        mock_request.assert_called_once_with(
            "POST", "/krstock/quote/v1/afterHoursCurrent", body={"iem_cd": "005930"}
        )
        assert result.symbol == "005930"
        assert result.price == Decimal("51000")
        assert result.volume == Decimal("1234")


@pytest.mark.asyncio
async def test_get_after_hours_current_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required ovtm_untp_prpr raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"iem_cd": "005930"}}

        with pytest.raises(FatalExchangeError, match="ovtm_untp_prpr"):
            await nh_adapter.get_after_hours_current("005930")


@pytest.mark.asyncio
async def test_get_after_hours_expected_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "iem_cd": "005930",
                    "bsop_hour": "160000",
                    "stck_prpr": "51000",
                    "cntg_vol": "10",
                    "askp1": "51100",
                    "bidp1": "50900",
                }
            ]
        }
        result = await nh_adapter.get_after_hours_expected("005930")

        assert len(result) == 1
        assert result[0].time == "160000"
        assert result[0].price == Decimal("51000")


@pytest.mark.asyncio
async def test_get_after_hours_expected_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required bsop_hour raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [{"stck_prpr": "51000", "cntg_vol": "10"}]}

        with pytest.raises(FatalExchangeError, match="bsop_hour"):
            await nh_adapter.get_after_hours_expected("005930")


@pytest.mark.asyncio
async def test_get_after_hours_daily_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "qry_date": "20260924",
                    "qry_time": "160000",
                    "stck_prpr": "51000",
                    "hts_kor_isnm": "삼성전자",
                }
            ],
            "Output_1": [{"acml_vol": "1000", "acml_tr_pbmn": "51000000", "prdy_ctrt": "1.0"}],
        }
        result = await nh_adapter.get_after_hours_daily("005930", date="20260924")

        mock_request.assert_called_once_with(
            "POST",
            "/krstock/quote/v1/currentAfterHoursDaily",
            body={
                "iem_cd": "005930",
                "date": "20260924",
                "array_cnt": "30",
                "maxavg": "0",
                "gubun": "1",
            },
        )
        assert len(result) == 1
        assert result[0].date == "20260924"
        assert result[0].volume == Decimal("1000")


@pytest.mark.asyncio
async def test_get_after_hours_daily_length_mismatch_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: Output_0/Output_1 length mismatch raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [{"qry_date": "20260924"}],
            "Output_1": [],
        }

        with pytest.raises(FatalExchangeError, match="mismatch"):
            await nh_adapter.get_after_hours_daily("005930", date="20260924")


@pytest.mark.asyncio
async def test_get_after_hours_execution_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "iem_cd": "005930",
                    "bsop_hour": "160000",
                    "stck_prpr": "51000",
                    "cntg_vol": "10",
                    "acml_vol": "1000",
                }
            ]
        }
        result = await nh_adapter.get_after_hours_execution("005930")

        assert len(result) == 1
        assert result[0].accumulated_volume == Decimal("1000")


@pytest.mark.asyncio
async def test_get_after_hours_execution_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required stck_prpr raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [{"bsop_hour": "160000", "cntg_vol": "10"}]}

        with pytest.raises(FatalExchangeError, match="stck_prpr"):
            await nh_adapter.get_after_hours_execution("005930")


@pytest.mark.asyncio
async def test_get_current_execution_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "bsop_hour": "093000",
                    "cntg_vol": "5",
                    "acml_vol": "100",
                    "askp": "51100",
                    "bidp": "50900",
                    "cttr": "120.5",
                }
            ]
        }
        result = await nh_adapter.get_current_execution("005930")

        mock_request.assert_called_once_with(
            "POST",
            "/krstock/quote/v1/currentExecution",
            body={
                "iem_cd": "005930",
                "market_cd": "KRX",
                "view_main_yn": "Y",
                "array_cnt": "30",
            },
        )
        assert result[0].changed_volume == Decimal("5")


@pytest.mark.asyncio
async def test_get_current_execution_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required cntg_vol raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [{"bsop_hour": "093000"}]}

        with pytest.raises(FatalExchangeError, match="cntg_vol"):
            await nh_adapter.get_current_execution("005930")


@pytest.mark.asyncio
async def test_get_current_investor_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "bsop_date1": "20260924",
                    "acml_vol": "1000",
                    "person": "100",
                    "frgn_ntby_qty": "-50",
                    "gigwan": "-50",
                }
            ]
        }
        result = await nh_adapter.get_current_investor("005930")

        assert result[0].trade_date == "20260924"
        assert result[0].foreign_net_buy == Decimal("-50")


@pytest.mark.asyncio
async def test_get_current_investor_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required bsop_date1 raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [{"acml_vol": "1000"}]}

        with pytest.raises(FatalExchangeError, match="bsop_date1"):
            await nh_adapter.get_current_investor("005930")


@pytest.mark.asyncio
async def test_get_etf_components_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "iem_cd": "005930",
                    "iem_nm": "삼성전자",
                    "stck_prpr": "51000",
                    "cu_unit": "100",
                    "vol": "5.5",
                }
            ]
        }
        result = await nh_adapter.get_etf_components("069500")

        mock_request.assert_called_once_with(
            "POST", "/krstock/quote/v1/etfComponents", body={"iem_cd": "069500"}
        )
        assert result[0].symbol == "005930"
        assert result[0].weight == Decimal("5.5")


@pytest.mark.asyncio
async def test_get_etf_components_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required stck_prpr raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [{"iem_cd": "005930"}]}

        with pytest.raises(FatalExchangeError, match="stck_prpr"):
            await nh_adapter.get_etf_components("069500")


@pytest.mark.asyncio
async def test_get_etf_current_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {
                "iem_cd": "069500",
                "iem_nm": "KODEX 200",
                "stck_prpr": "35000",
                "acml_vol": "100000",
                "askp1": "35050",
                "bidp1": "34950",
            }
        }
        result = await nh_adapter.get_etf_current("069500")

        assert result.symbol == "069500"
        assert result.price == Decimal("35000")


@pytest.mark.asyncio
async def test_get_etf_current_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required stck_prpr raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"iem_cd": "069500"}}

        with pytest.raises(FatalExchangeError, match="stck_prpr"):
            await nh_adapter.get_etf_current("069500")


@pytest.mark.asyncio
async def test_get_period_quote_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"iem_cd": "005930", "stck_prpr": "51000"},
            "Output_1": [
                {
                    "bsop_date": "20260924",
                    "stck_oprc": "50000",
                    "stck_hgpr": "51500",
                    "stck_lwpr": "49800",
                    "stck_prpr": "51000",
                    "vol": "1000000",
                    "tr_pbmn": "51000000000",
                }
            ],
        }
        request = PeriodQuoteRequest(symbol="005930", period_code="1")
        result = await nh_adapter.get_period_quote(request)

        mock_request.assert_called_once_with(
            "POST",
            "/krstock/quote/v1/period",
            body={
                "iem_cd": "005930",
                "market_cd": "KRX",
                "view_main_yn": "Y",
                "gubun": "1",
            },
        )
        assert len(result) == 1
        assert result[0].close == Decimal("51000")
        assert result[0].open == Decimal("50000")


@pytest.mark.asyncio
async def test_get_period_quote_missing_field_raises(nh_adapter: NHAdapter) -> None:
    """Negative test: missing required stck_hgpr raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {},
            "Output_1": [{"bsop_date": "20260924", "stck_oprc": "50000"}],
        }
        request = PeriodQuoteRequest(symbol="005930")

        with pytest.raises(FatalExchangeError, match="stck_hgpr"):
            await nh_adapter.get_period_quote(request)


@pytest.mark.asyncio
async def test_get_period_quote_with_optional_fields(nh_adapter: NHAdapter) -> None:
    """Contract: end_date/array_cnt are passed through when provided."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {}, "Output_1": []}
        request = PeriodQuoteRequest(
            symbol="005930", period_code="3", end_date="20260924", array_cnt="10"
        )

        await nh_adapter.get_period_quote(request)

        body = mock_request.call_args[1]["body"]
        assert body["gubun"] == "3"
        assert body["edate"] == "20260924"
        assert body["array_cnt"] == "10"


@pytest.mark.asyncio
async def test_quote_extra_endpoints_http_error_injection(nh_adapter: NHAdapter) -> None:
    """Failure-injection: HTTP errors propagate for the new quote endpoints too."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = Exception("HTTP 500: Internal Server Error")

        with pytest.raises(Exception, match="HTTP 500"):
            await nh_adapter.get_after_hours_current("005930")
        with pytest.raises(Exception, match="HTTP 500"):
            await nh_adapter.get_etf_current("069500")


@pytest.mark.asyncio
async def test_quote_extra_endpoints_empty_response(nh_adapter: NHAdapter) -> None:
    """Edge case: empty array responses return empty lists, not errors."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": []}

        assert await nh_adapter.get_after_hours_expected("005930") == []
        assert await nh_adapter.get_current_execution("005930") == []
        assert await nh_adapter.get_current_investor("005930") == []
        assert await nh_adapter.get_etf_components("069500") == []
