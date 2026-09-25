"""NH krstock/inquiry 확장 조회 10종 계약 테스트 — BR-20b(task-7009).

Split out of test_nh_contract_generated.py (§7 파일 정책 — line-cap 500
초과 예방, `check_code_ratchets.py`의 loc_over_500 게이트) into its own file
because it covers a distinct set of endpoints (`inquiry_mixin.py`) from
that file's market-data focus (`get_ohlcv`). Fixtures derived from
`docs/design/nh_openapi_reference.json` (official openapi.json snapshot);
does NOT hit the real API.
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.nh.adapter import NHAdapter
from src.exchanges.nh.inquiry_dto import (
    AssetStatusRequest,
    BuyableQuantityRequest,
    DailyPnlRequest,
    RealizedPnlRequest,
    ReservedInquiryRequest,
    RightsHeldRequest,
    SellableQuantityRequest,
    TradingPnlRequest,
)


@pytest.fixture
def nh_adapter() -> NHAdapter:
    """Minimal NHAdapter for testing."""
    return NHAdapter(app_key="test_key", app_secret="test_secret", act_no="")


@pytest.mark.asyncio
async def test_get_asset_status_happy_path(nh_adapter: NHAdapter) -> None:
    """Contract: assetStatus parses summary(Output_0) + holdings(Output_1)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"dca": "1000000", "tot_aet_amt": "5000000", "tot_eal_amt": "4000000"},
            "Output_1": [
                {"iem_cd": "005930", "iem_nm": "삼성전자", "itg_bnc_qty": "10", "eal_amt": "700000"}
            ],
            "message": "ok",
        }
        result = await nh_adapter.get_asset_status(AssetStatusRequest())
        assert result.deposit == Decimal("1000000")
        assert result.total_asset_amount == Decimal("5000000")
        assert len(result.holdings) == 1
        assert result.holdings[0].symbol == "005930"
        mock_request.assert_called_once()
        assert mock_request.call_args[0][1] == "/krstock/inquiry/v1/assetStatus"


@pytest.mark.asyncio
async def test_get_asset_status_missing_required_field(nh_adapter: NHAdapter) -> None:
    """Negative test: missing dca raises FatalExchangeError (no silent 0-fill)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"tot_aet_amt": "5000000"}, "message": "ok"}
        with pytest.raises(FatalExchangeError, match="dca"):
            await nh_adapter.get_asset_status(AssetStatusRequest())


@pytest.mark.asyncio
async def test_get_buyable_quantity_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"csh_orr_pbl_qty": "100", "csh_orr_pbl_amt": "5000000"},
            "message": "ok",
        }
        result = await nh_adapter.get_buyable_quantity(
            BuyableQuantityRequest(symbol="005930", division_code="1", order_price_type_code="01")
        )
        assert result.cash_buyable_quantity == Decimal("100")
        body = mock_request.call_args[1]["body"]
        assert body["iem_cd"] == "005930"
        assert body["ost_dit_cd"] == "1"


@pytest.mark.asyncio
async def test_get_buyable_quantity_missing_required_field(nh_adapter: NHAdapter) -> None:
    """Negative test: missing csh_orr_pbl_qty raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"csh_orr_pbl_amt": "5000000"}, "message": "ok"}
        with pytest.raises(FatalExchangeError, match="csh_orr_pbl_qty"):
            await nh_adapter.get_buyable_quantity(
                BuyableQuantityRequest(
                    symbol="005930", division_code="1", order_price_type_code="01"
                )
            )


@pytest.mark.asyncio
async def test_get_daily_pnl_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"pls_amt_sum": "150000", "byn_cst_sum": "1000000"},
            "Output_1": [
                {
                    "sby_dt": "20260924",
                    "byn_amt": "500000",
                    "sll_amt": "650000",
                    "pls_amt": "150000",
                }
            ],
            "message": "ok",
        }
        result = await nh_adapter.get_daily_pnl(
            DailyPnlRequest(start_date="20260901", end_date="20260924")
        )
        assert result.total_profit_amount == Decimal("150000")
        assert len(result.rows) == 1
        assert result.rows[0].trade_date == "20260924"


@pytest.mark.asyncio
async def test_get_integrated_margin_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"lmt_amt": "10000000", "lmt_use_amt": "2000000"},
            "message": "ok",
        }
        result = await nh_adapter.get_integrated_margin()
        assert result.limit_amount == Decimal("10000000")
        assert mock_request.call_args[1]["body"] == {"act_no": nh_adapter._act_no}


@pytest.mark.asyncio
async def test_get_realized_pnl_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"eal_amt_sum": "3000000", "eal_pls_amt": "200000"},
            "Output_1": [
                {
                    "iem_cd": "005930",
                    "iem_nm": "삼성전자",
                    "itg_bnc_qty": "10",
                    "rzt_pls_amt": "50000",
                }
            ],
            "message": "ok",
        }
        result = await nh_adapter.get_realized_pnl(
            RealizedPnlRequest(inquiry_division_code="0", fee_division_code="1")
        )
        assert result.eval_amount_sum == Decimal("3000000")
        assert result.holdings[0].symbol == "005930"


@pytest.mark.asyncio
async def test_get_reserved_orders_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"tab_nm": "예약주문"},
            "Output_1": [
                {"bkg_rtn_orr_no": "123456", "iem_cd": "005930", "orr_qty": "10", "orr_pr": "70000"}
            ],
            "message": "ok",
        }
        rows = await nh_adapter.get_reserved_orders(
            ReservedInquiryRequest(side_code="0", reserved_order_type_code="0")
        )
        assert len(rows) == 1
        assert rows[0].reserved_order_no == "123456"


@pytest.mark.asyncio
async def test_get_reserved_orders_missing_required_field(nh_adapter: NHAdapter) -> None:
    """Negative test: missing bkg_rtn_orr_no raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_1": [{"iem_cd": "005930"}], "message": "ok"}
        with pytest.raises(FatalExchangeError, match="bkg_rtn_orr_no"):
            await nh_adapter.get_reserved_orders(
                ReservedInquiryRequest(side_code="0", reserved_order_type_code="0")
            )


@pytest.mark.asyncio
async def test_get_rights_held_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"sta_dt": "20260101"},
            "Output_1": [{"iem_cd": "005930", "iem_nm": "삼성전자", "hld_qty": "10"}],
            "message": "ok",
        }
        rows = await nh_adapter.get_rights_held(RightsHeldRequest())
        assert rows[0].symbol == "005930"
        assert rows[0].held_quantity == Decimal("10")


@pytest.mark.asyncio
async def test_get_rights_scheduled_happy_path(nh_adapter: NHAdapter) -> None:
    """Contract: rightsScheduled's Output_0 is itself the array (no Output_1,
    unlike the other 9 endpoints -- see inquiry_dto.py's RightsScheduledRow)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [{"iem_cd": "005930", "iem_nm": "삼성전자", "aloc_qty": "5"}],
            "message": "ok",
        }
        rows = await nh_adapter.get_rights_scheduled()
        assert len(rows) == 1
        assert rows[0].symbol == "005930"
        assert rows[0].allocated_quantity == Decimal("5")


@pytest.mark.asyncio
async def test_get_rights_scheduled_output0_not_array(nh_adapter: NHAdapter) -> None:
    """Negative test: Output_0 as an object (not array) raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"sta_dt": "20260101"}, "message": "ok"}
        with pytest.raises(FatalExchangeError, match="not an array"):
            await nh_adapter.get_rights_scheduled()


@pytest.mark.asyncio
async def test_get_sellable_quantity_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"sll_pbl_qty": "10", "bnc_qty": "10", "phs_uit_pr": "70000"},
            "message": "ok",
        }
        result = await nh_adapter.get_sellable_quantity(
            SellableQuantityRequest(symbol="005930", credit_loan_code="00")
        )
        assert result.sellable_quantity == Decimal("10")


@pytest.mark.asyncio
async def test_get_sellable_quantity_missing_required_field(nh_adapter: NHAdapter) -> None:
    """Negative test: missing sll_pbl_qty raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"bnc_qty": "10"}, "message": "ok"}
        with pytest.raises(FatalExchangeError, match="sll_pbl_qty"):
            await nh_adapter.get_sellable_quantity(
                SellableQuantityRequest(symbol="005930", credit_loan_code="00")
            )


@pytest.mark.asyncio
async def test_get_trading_pnl_happy_path(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"pls_amt": "300000", "fee_sum": "5000"},
            "Output_1": [
                {"iem_cd": "005930", "iem_nm": "삼성전자", "pls_amt": "300000", "pft_rt": "5.5"}
            ],
            "message": "ok",
        }
        result = await nh_adapter.get_trading_pnl(
            TradingPnlRequest(start_date="20260901", end_date="20260924")
        )
        assert result.total_profit_amount == Decimal("300000")
        assert result.rows[0].symbol == "005930"


@pytest.mark.asyncio
async def test_inquiry_http_error_injection(nh_adapter: NHAdapter) -> None:
    """Failure-injection: HTTP/transport errors propagate as-is (no silent
    swallow) for the new inquiry endpoints."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = FatalExchangeError("HTTP 500: Internal Server Error")
        with pytest.raises(FatalExchangeError, match="HTTP 500"):
            await nh_adapter.get_integrated_margin()


@pytest.mark.asyncio
async def test_get_daily_pnl_response_parsing_performance(nh_adapter: NHAdapter) -> None:
    """Performance assertion: parsing 200 daily-pnl rows stays under the
    <100ms adapter-parsing budget (ADR-2026-09-09-C Decision 1 default)."""
    large_response = {
        "Output_0": {"pls_amt_sum": "150000"},
        "Output_1": [
            {
                "sby_dt": f"2026{1 + (i % 12):02d}{1 + (i % 28):02d}",
                "byn_amt": "500000",
                "sll_amt": "650000",
                "pls_amt": "150000",
            }
            for i in range(200)
        ],
        "message": "ok",
    }
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = large_response

        start = time.perf_counter()
        result = await nh_adapter.get_daily_pnl(
            DailyPnlRequest(start_date="20260101", end_date="20260924")
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(result.rows) == 200
        assert elapsed_ms < 100, (
            f"Parsing 200 dailyPnl rows took {elapsed_ms:.1f}ms (budget: <100ms)"
        )
