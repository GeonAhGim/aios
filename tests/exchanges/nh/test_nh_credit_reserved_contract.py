"""NH Adapter 계약 테스트 — BR-20c(task-7010).

`/krstock/order/*` creditBuy/creditSell/reservedOrder/reservedCancel 4종.
Uses mocked `_request`; does NOT hit real API. Separate file from
`test_nh_contract_generated.py`(market-data) to keep both under the
500-line ratchet (scripts/check_code_ratchets.py `loc_over_500`).

All four methods carry `@require_paper_sandbox` (NHAdapter.is_paper_trading/
is_sandboxed are always False, per adapter.py) so the guard always blocks
them -- the same structural situation as place_order/cancel_order/
modify_order in tests/integration/test_nh_adapter.py. Business-logic tests
below call the undecorated function via functools.wraps' `__wrapped__`
(test-only bypass, guard coverage itself is asserted by
test_live_guard_coverage.py).
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide, OrderType
from src.exchanges.nh.adapter import NHAdapter
from src.exchanges.nh.credit_reserved_dto import (
    CreditOrderRequest,
    ReservedCancelRequest,
    ReservedOrderRequest,
)


@pytest.fixture
def nh_adapter() -> NHAdapter:
    """Minimal NHAdapter for testing."""
    return NHAdapter(app_key="test_key", app_secret="test_secret", act_no="1234567890")


async def _unguarded_place_credit_order(adapter: NHAdapter, request: CreditOrderRequest):
    unwrapped = NHAdapter.place_credit_order.__wrapped__
    return await unwrapped(adapter, request)


async def _unguarded_place_reserved_order(adapter: NHAdapter, request: ReservedOrderRequest):
    unwrapped = NHAdapter.place_reserved_order.__wrapped__
    return await unwrapped(adapter, request)


async def _unguarded_cancel_reserved_order(adapter: NHAdapter, request: ReservedCancelRequest):
    unwrapped = NHAdapter.cancel_reserved_order.__wrapped__
    return await unwrapped(adapter, request)


def _credit_order_request(**overrides) -> CreditOrderRequest:
    defaults = dict(
        symbol="005930",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        credit_loan_code="01",
        price=Decimal("71000"),
    )
    defaults.update(overrides)
    return CreditOrderRequest(**defaults)


def _reserved_order_request(**overrides) -> ReservedOrderRequest:
    defaults = dict(
        symbol="005930",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        reserved_order_type_code="1",
        execution_type_code="1",
        price=Decimal("71000"),
    )
    defaults.update(overrides)
    return ReservedOrderRequest(**defaults)


def _reserved_cancel_request(**overrides) -> ReservedCancelRequest:
    defaults = dict(
        symbol="005930",
        side=OrderSide.BUY,
        reserved_order_no="555",
        reserved_order_type_code="1",
    )
    defaults.update(overrides)
    return ReservedCancelRequest(**defaults)


# ---------- place_credit_order: creditBuy/creditSell ----------


@pytest.mark.asyncio
async def test_place_credit_order_buy_uses_credit_buy_endpoint(nh_adapter: NHAdapter) -> None:
    """Red-gate: before task-7010, creditBuy/creditSell were 미착수(not started)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"mkt_orr_no": 12345}, "message": "ok"}

        result = await _unguarded_place_credit_order(nh_adapter, _credit_order_request())

        call_args = mock_request.call_args
        assert call_args[0][1] == "/krstock/order/v1/creditBuy"
        assert call_args[1]["body"]["nmn_pr_tp_cd"] == "01"
        assert call_args[1]["body"]["cfd_lon_cd"] == "01"
        assert result.mkt_orr_no == "12345"


@pytest.mark.asyncio
async def test_place_credit_order_sell_uses_credit_sell_endpoint(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"mkt_orr_no": 12345}, "message": "ok"}

        await _unguarded_place_credit_order(nh_adapter, _credit_order_request(side=OrderSide.SELL))

        assert mock_request.call_args[0][1] == "/krstock/order/v1/creditSell"


@pytest.mark.asyncio
async def test_place_credit_order_market_type_uses_market_division_code(
    nh_adapter: NHAdapter,
) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"mkt_orr_no": 12345}, "message": "ok"}

        await _unguarded_place_credit_order(
            nh_adapter, _credit_order_request(order_type=OrderType.MARKET, price=None)
        )

        body = mock_request.call_args[1]["body"]
        assert body["nmn_pr_tp_cd"] == "05"
        assert "orr_pr" not in body


# ---------- place_reserved_order: reservedOrder ----------


@pytest.mark.asyncio
async def test_place_reserved_order_sends_expected_body(nh_adapter: NHAdapter) -> None:
    """Red-gate: before task-7010, reservedOrder was 미착수(not started)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"bkg_orr_no": 777, "iem_cd": "005930", "sby_dit_cd": "2"},
            "message": "ok",
        }

        result = await _unguarded_place_reserved_order(nh_adapter, _reserved_order_request())

        call_args = mock_request.call_args
        assert call_args[0][1] == "/krstock/order/v1/reservedOrder"
        body = call_args[1]["body"]
        assert body["sby_dit_cd"] == "2"  # 2=매수
        assert body["bkg_orr_tp_cd"] == "1"
        assert result.reserved_order_no == "777"


@pytest.mark.asyncio
async def test_place_reserved_order_sell_sets_sby_dit_cd_1(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"bkg_orr_no": 777}, "message": "ok"}

        await _unguarded_place_reserved_order(
            nh_adapter, _reserved_order_request(side=OrderSide.SELL)
        )

        assert mock_request.call_args[1]["body"]["sby_dit_cd"] == "1"  # 1=매도


# ---------- cancel_reserved_order: reservedCancel ----------


@pytest.mark.asyncio
async def test_cancel_reserved_order_sends_expected_body(nh_adapter: NHAdapter) -> None:
    """Red-gate: before task-7010, reservedCancel was 미착수(not started)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": {"bkg_orr_no": 555, "iem_cd": "005930", "sby_dit_cd": "2"},
            "message": "ok",
        }

        result = await _unguarded_cancel_reserved_order(nh_adapter, _reserved_cancel_request())

        call_args = mock_request.call_args
        assert call_args[0][1] == "/krstock/order/v1/reservedCancel"
        assert call_args[1]["body"]["bkg_orr_no"] == 555
        assert result.reserved_order_no == "555"


# ============================================================================
# Negative tests (>=3 required per D2, order group)
# ============================================================================


@pytest.mark.asyncio
async def test_place_credit_order_raises_fatal_when_loan_date_missing_for_code_03(
    nh_adapter: NHAdapter,
) -> None:
    """Negative 1: cfd_lon_cd=03(유통대주) without lon_dt is rejected fail-closed."""
    with pytest.raises(FatalExchangeError, match="loan_date"):
        await _unguarded_place_credit_order(
            nh_adapter, _credit_order_request(credit_loan_code="03", loan_date=None)
        )


@pytest.mark.asyncio
async def test_place_credit_order_raises_fatal_when_response_field_missing(
    nh_adapter: NHAdapter,
) -> None:
    """Negative 2: response missing mkt_orr_no raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"unexpected": "1"}, "message": "ok"}

        with pytest.raises(FatalExchangeError):
            await _unguarded_place_credit_order(nh_adapter, _credit_order_request())


@pytest.mark.asyncio
async def test_place_reserved_order_raises_fatal_when_date_range_missing_for_type_2(
    nh_adapter: NHAdapter,
) -> None:
    """Negative 3: bkg_orr_tp_cd=2(잔량주문) without start/end date is rejected."""
    with pytest.raises(FatalExchangeError, match="start_date"):
        await _unguarded_place_reserved_order(
            nh_adapter, _reserved_order_request(reserved_order_type_code="2")
        )


@pytest.mark.asyncio
async def test_cancel_reserved_order_raises_fatal_when_receipt_date_missing_for_type_3(
    nh_adapter: NHAdapter,
) -> None:
    """Negative 4: bkg_orr_tp_cd=3(지정수량주문) without bkg_rtn_dt is rejected."""
    with pytest.raises(FatalExchangeError, match="reserved_receipt_date"):
        await _unguarded_cancel_reserved_order(
            nh_adapter, _reserved_cancel_request(reserved_order_type_code="3")
        )


@pytest.mark.asyncio
async def test_cancel_reserved_order_raises_fatal_on_non_numeric_reserved_order_no(
    nh_adapter: NHAdapter,
) -> None:
    """Negative 5: malformed reserved_order_no fails closed instead of guessing."""
    with pytest.raises(FatalExchangeError, match="bkg_orr_no"):
        await _unguarded_cancel_reserved_order(
            nh_adapter, _reserved_cancel_request(reserved_order_no="not-a-number")
        )


# ============================================================================
# Failure-injection test (>=1 required per D2, order group)
# ============================================================================


@pytest.mark.asyncio
async def test_place_credit_order_http_error_injection(nh_adapter: NHAdapter) -> None:
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = Exception("HTTP 500: Internal Server Error")

        with pytest.raises(Exception, match="HTTP 500"):
            await _unguarded_place_credit_order(nh_adapter, _credit_order_request())


# ============================================================================
# Performance assertion (>=1 required per D2, order group)
# ============================================================================


@pytest.mark.asyncio
async def test_place_reserved_order_response_parsing_performance(nh_adapter: NHAdapter) -> None:
    """Budget: order-path DTO construction should stay well under 50ms
    (ADR-2026-09-09-C Decision 1 default adapter-parsing budget)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": {"bkg_orr_no": 777}, "message": "ok"}

        start = time.perf_counter()
        await _unguarded_place_reserved_order(nh_adapter, _reserved_order_request())
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, f"place_reserved_order took {elapsed_ms:.1f}ms (budget: <50ms)"
