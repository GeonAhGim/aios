"""NH Adapter 계약 테스트 (generated) — Task-6695(BR-17).

Contract tests for newly implemented NH market-data endpoints.
Uses fixtures derived from openapi.json schemas; does NOT hit real API.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle
from src.exchanges.nh.adapter import NHAdapter

# ============================================================================
# Fixtures: Mock HTTP responses based on NH openapi.json schemas
# ============================================================================


@pytest.fixture
def nh_adapter() -> NHAdapter:
    """Minimal NHAdapter for testing."""
    return NHAdapter(
        app_key="test_key",
        app_secret="test_secret",
        act_no="",  # Optional for market-data-only tests
    )


@pytest.fixture
def mock_current_daily_response() -> dict:
    """Mock response for /krstock/quote/v1/currentDaily.

    Based on NH openapi.json schema: Output_0 is an array of daily candles.
    Fields extracted from schema: bsop_date, stck_oppr, stck_hgpr, stck_lwpr,
    stck_clpr, acml_vol, and others (not all required for Candle mapping).
    """
    return {
        "Output_0": [
            {
                "bsop_date": "20260924",
                "stck_oppr": "50000",
                "stck_hgpr": "51000",
                "stck_lwpr": "49500",
                "stck_clpr": "50500",
                "acml_vol": "1000000",
                "cttr": "505000000000",  # 체결금액
                "prdy_clpr": "49500",
                "prdy_ctrt": "0",  # 전일대비
                "prdy_vrss": "1000",  # 전일대비금액
                "filler": "",  # Optional field
            },
            {
                "bsop_date": "20260923",
                "stck_oppr": "49200",
                "stck_hgpr": "49800",
                "stck_lwpr": "48500",
                "stck_clpr": "49500",
                "acml_vol": "950000",
                "cttr": "474750000000",
                "prdy_clpr": "48700",
                "prdy_ctrt": "0",
                "prdy_vrss": "800",
                "filler": "",
            },
        ],
        "message": "ok",
    }


@pytest.fixture
def mock_current_daily_empty_volume() -> dict:
    """Mock response with zero/missing volume."""
    return {
        "Output_0": [
            {
                "bsop_date": "20260924",
                "stck_oppr": "50000",
                "stck_hgpr": "51000",
                "stck_lwpr": "49500",
                "stck_clpr": "50500",
                # acml_vol missing — test defaults to "0"
                "cttr": "505000000000",
            },
        ],
        "message": "ok",
    }


@pytest.fixture
def mock_current_daily_missing_date() -> dict:
    """Mock response with missing required bsop_date field."""
    return {
        "Output_0": [
            {
                # bsop_date missing
                "stck_oppr": "50000",
                "stck_hgpr": "51000",
                "stck_lwpr": "49500",
                "stck_clpr": "50500",
                "acml_vol": "1000000",
            },
        ],
        "message": "ok",
    }


@pytest.fixture
def mock_current_daily_missing_price() -> dict:
    """Mock response with missing required price field."""
    return {
        "Output_0": [
            {
                "bsop_date": "20260924",
                "stck_oppr": "50000",
                "stck_hgpr": "51000",
                # stck_lwpr missing
                "stck_clpr": "50500",
                "acml_vol": "1000000",
            },
        ],
        "message": "ok",
    }


# ============================================================================
# Tests: Basic functionality (red-gate reproduction)
# ============================================================================


@pytest.mark.asyncio
async def test_get_ohlcv_implementation_present(nh_adapter: NHAdapter) -> None:
    """Red-gate: Before task-6695, get_ohlcv raised NotImplementedError.

    Now it should not raise NotImplementedError on proper input.
    (This test reproduces the gate that task-6695 closes.)
    """
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_response = {
            "Output_0": [
                {
                    "bsop_date": "20260924",
                    "stck_oppr": "50000",
                    "stck_hgpr": "51000",
                    "stck_lwpr": "49500",
                    "stck_clpr": "50500",
                    "acml_vol": "1000000",
                },
            ],
            "message": "ok",
        }
        mock_request.return_value = mock_response

        # Should NOT raise NotImplementedError
        result = await nh_adapter.get_ohlcv("005930", "1d", limit=1)

        assert len(result) == 1
        assert isinstance(result[0], Candle)
        assert result[0].symbol == "005930"
        assert result[0].close == Decimal("50500")


@pytest.mark.asyncio
async def test_get_ohlcv_returns_candles(
    nh_adapter: NHAdapter, mock_current_daily_response: dict
) -> None:
    """Basic functionality: get_ohlcv returns list of Candle objects."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_response

        result = await nh_adapter.get_ohlcv("005930", "1d", limit=2)

        assert len(result) == 2
        assert all(isinstance(c, Candle) for c in result)


@pytest.mark.asyncio
async def test_get_ohlcv_field_mapping(
    nh_adapter: NHAdapter, mock_current_daily_response: dict
) -> None:
    """Contract: Response fields map to Candle fields correctly."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_response

        result = await nh_adapter.get_ohlcv("005930", "1d", limit=1)
        candle = result[0]

        # Field mappings per openapi.json schema
        assert candle.symbol == "005930"
        assert candle.exchange == "nh"
        assert candle.timeframe == "1d"
        assert candle.open == Decimal("50000")  # stck_oppr
        assert candle.high == Decimal("51000")  # stck_hgpr
        assert candle.low == Decimal("49500")  # stck_lwpr
        assert candle.close == Decimal("50500")  # stck_clpr
        assert candle.volume == Decimal("1000000")  # acml_vol
        # Timestamps: both set to same date (bsop_date)
        assert candle.open_time.date().isoformat() == "2026-09-24"
        assert candle.close_time.date().isoformat() == "2026-09-24"
        assert candle.open_time.tzinfo == timezone.utc
        assert candle.close_time.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_get_ohlcv_date_parsing(
    nh_adapter: NHAdapter, mock_current_daily_response: dict
) -> None:
    """Contract: bsop_date (YYYYMMDD format) parses correctly."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_response

        result = await nh_adapter.get_ohlcv("005930", "1d", limit=2)

        # First candle: 2026-09-24
        assert result[0].open_time == datetime(2026, 9, 24, tzinfo=timezone.utc)

        # Second candle: 2026-09-23
        assert result[1].open_time == datetime(2026, 9, 23, tzinfo=timezone.utc)


# ============================================================================
# Negative tests (≥3 required per D2)
# ============================================================================


@pytest.mark.asyncio
async def test_get_ohlcv_unsupported_timeframe() -> None:
    """Negative test 1: Unsupported timeframe raises ValueError."""
    adapter = NHAdapter(app_key="test_key", app_secret="test_secret", act_no="")

    with pytest.raises(ValueError, match="only daily"):
        await adapter.get_ohlcv("005930", "1h", limit=100)


@pytest.mark.asyncio
async def test_get_ohlcv_missing_required_date_field(
    nh_adapter: NHAdapter, mock_current_daily_missing_date: dict
) -> None:
    """Negative test 2: Missing required bsop_date field raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_missing_date

        with pytest.raises(FatalExchangeError, match="bsop_date"):
            await nh_adapter.get_ohlcv("005930", "1d", limit=1)


@pytest.mark.asyncio
async def test_get_ohlcv_missing_required_price_field(
    nh_adapter: NHAdapter, mock_current_daily_missing_price: dict
) -> None:
    """Negative test 3: Missing required price field raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_missing_price

        with pytest.raises(FatalExchangeError, match="stck"):
            await nh_adapter.get_ohlcv("005930", "1d", limit=1)


@pytest.mark.asyncio
async def test_get_ohlcv_invalid_date_format(nh_adapter: NHAdapter) -> None:
    """Negative test 4: Invalid bsop_date format raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "Output_0": [
                {
                    "bsop_date": "invalid-date",  # not YYYYMMDD
                    "stck_oppr": "50000",
                    "stck_hgpr": "51000",
                    "stck_lwpr": "49500",
                    "stck_clpr": "50500",
                    "acml_vol": "1000000",
                },
            ],
            "message": "ok",
        }

        with pytest.raises(FatalExchangeError):
            await nh_adapter.get_ohlcv("005930", "1d", limit=1)


# ============================================================================
# Failure-injection test (≥1 required per D2)
# ============================================================================


@pytest.mark.asyncio
async def test_get_ohlcv_http_error_injection(nh_adapter: NHAdapter) -> None:
    """Failure-injection: HTTP errors (e.g., 500) propagate as exceptions."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = Exception("HTTP 500: Internal Server Error")

        with pytest.raises(Exception, match="HTTP 500"):
            await nh_adapter.get_ohlcv("005930", "1d", limit=1)


@pytest.mark.asyncio
async def test_get_ohlcv_malformed_response_injection(
    nh_adapter: NHAdapter,
) -> None:
    """Failure-injection: Malformed JSON response raises FatalExchangeError."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        # Output_0 items missing the required bsop_date field
        mock_request.return_value = {"Output_0": [{"stck_clpr": "50500"}]}

        with pytest.raises(FatalExchangeError):
            await nh_adapter.get_ohlcv("005930", "1d", limit=1)


# ============================================================================
# Performance assertion (≥1 required per D2)
# ============================================================================


@pytest.mark.perf
@pytest.mark.asyncio
async def test_get_ohlcv_response_parsing_performance(
    nh_adapter: NHAdapter,
) -> None:
    """Performance assertion: Parsing 100 candles should be fast (p95 < 100ms).

    Task-6695 markets data endpoints should have <100ms latency (per
    ADR-2026-09-09-C Decision 1 default budget for adapter parsing).
    """
    large_response = {
        "Output_0": [
            {
                "bsop_date": f"2026{1 + (i % 12):02d}{1 + (i % 28):02d}",
                "stck_oppr": "50000",
                "stck_hgpr": "51000",
                "stck_lwpr": "49500",
                "stck_clpr": "50500",
                "acml_vol": "1000000",
                "cttr": "505000000000",
            }
            for i in range(100)
        ],
        "message": "ok",
    }

    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = large_response

        start = time.perf_counter()
        result = await nh_adapter.get_ohlcv("005930", "1d", limit=100)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(result) == 100
        assert elapsed_ms < 100, f"Parsing 100 candles took {elapsed_ms:.1f}ms (budget: <100ms)"


# ============================================================================
# Edge cases and boundary conditions
# ============================================================================


@pytest.mark.asyncio
async def test_get_ohlcv_empty_response(nh_adapter: NHAdapter) -> None:
    """Edge case: Empty response (no data) returns empty list."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [], "message": "ok"}

        result = await nh_adapter.get_ohlcv("005930", "1d", limit=100)

        assert result == []


@pytest.mark.asyncio
async def test_get_ohlcv_zero_volume_handled(
    nh_adapter: NHAdapter, mock_current_daily_empty_volume: dict
) -> None:
    """Edge case: Missing acml_vol defaults to 0."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_current_daily_empty_volume

        result = await nh_adapter.get_ohlcv("005930", "1d", limit=1)

        assert len(result) == 1
        assert result[0].volume == Decimal("0")


@pytest.mark.asyncio
async def test_get_ohlcv_limit_parameter_passed(nh_adapter: NHAdapter) -> None:
    """Contract: limit parameter is passed to API request."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [], "message": "ok"}

        await nh_adapter.get_ohlcv("005930", "1d", limit=50)

        # Verify _request was called with correct parameters
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][1] == "/krstock/quote/v1/currentDaily"
        assert call_args[1]["body"]["array_cnt"] == 50


@pytest.mark.asyncio
async def test_get_ohlcv_request_body_format(nh_adapter: NHAdapter) -> None:
    """Contract: Request body matches NH API spec (Input_0 structure)."""
    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"Output_0": [], "message": "ok"}

        await nh_adapter.get_ohlcv("005930", "1d", limit=10)

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args[1]
        body = call_kwargs["body"]

        # Per openapi.json, Input_0 requires:
        assert body["iem_cd"] == "005930"
        assert body["market_cd"] == "KRX"
        assert body["view_main_yn"] == "Y"
        assert body["array_cnt"] == 10


@pytest.mark.asyncio
async def test_get_ohlcv_multiple_symbols(nh_adapter: NHAdapter) -> None:
    """Contract: Different symbols return different results."""
    symbol_responses = {
        "005930": {
            "Output_0": [
                {
                    "bsop_date": "20260924",
                    "stck_oppr": "50000",
                    "stck_hgpr": "51000",
                    "stck_lwpr": "49500",
                    "stck_clpr": "50500",
                    "acml_vol": "1000000",
                }
            ],
            "message": "ok",
        },
        "000660": {
            "Output_0": [
                {
                    "bsop_date": "20260924",
                    "stck_oppr": "60000",
                    "stck_hgpr": "61000",
                    "stck_lwpr": "59500",
                    "stck_clpr": "60500",
                    "acml_vol": "2000000",
                }
            ],
            "message": "ok",
        },
    }

    with patch.object(nh_adapter, "_request", new_callable=AsyncMock) as mock_request:

        async def get_response(*args, **kwargs):
            body = kwargs.get("body", {})
            symbol = body.get("iem_cd")
            return symbol_responses.get(symbol, {"Output_0": []})

        mock_request.side_effect = get_response

        result1 = await nh_adapter.get_ohlcv("005930", "1d", limit=1)
        result2 = await nh_adapter.get_ohlcv("000660", "1d", limit=1)

        assert result1[0].close == Decimal("50500")
        assert result2[0].close == Decimal("60500")
