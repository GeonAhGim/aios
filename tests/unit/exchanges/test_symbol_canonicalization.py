"""LA-19 — 거래소 심볼 변환의 `symbol_normalizer`(LA-7) 위임 검증.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-19.

FULL_AUDIT_2026-09-02.md §7 — 어댑터 자체 변환과 LA-7 단일 규칙이 따로 놀면
주문 시 "BTC/USDT", 조회 시 "BTCUSDT"처럼 조용히 어긋날 수 있다. 이 테스트는
(1) `src/exchanges/bitget/symbols.py`가 실제로 `symbol_normalizer`에 위임해
동일한 결과·동일한 예외를 내는지, (2) KIS market data 메서드가 형식이
잘못된 심볼을 조용히 거래소로 흘려보내지 않고 fail-closed 하는지 확인한다.
실거래소 호출은 없음 — httpx.MockTransport만 사용.
"""
from __future__ import annotations

import httpx
import pytest

from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.symbols import to_bitget_symbol, to_canonical_symbol
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_bitget_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _make_kis_adapter(handler) -> KISAdapter:
    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return handler(request)

    transport = httpx.MockTransport(_route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _fail_if_called(_request: httpx.Request) -> httpx.Response:
    pytest.fail("symbol validation must reject before any HTTP request is made")


# ---------- Bitget symbols.py delegates to symbol_normalizer(LA-7) ----------


def test_to_bitget_symbol_matches_normalizer() -> None:
    assert to_bitget_symbol("BTC/USDT") == to_venue(Venue.BITGET, "BTC/USDT") == "BTCUSDT"


def test_to_canonical_symbol_matches_normalizer() -> None:
    assert to_canonical_symbol("BTCUSDT") == to_canonical(Venue.BITGET, "BTCUSDT") == "BTC/USDT"


def test_to_bitget_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTC/XYZ")


def test_to_canonical_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical_symbol("BTCXYZ")


# ---------- Bitget market data round-trips through the shared normalizer ----------


async def test_bitget_get_ticker_request_uses_normalizer_raw_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPr": "80000",
                        "bidPr": "79990",
                        "askPr": "80010",
                        "baseVolume": "100",
                        "ts": "1000",
                    }
                ],
            },
        )

    adapter = _make_bitget_adapter(handler)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.symbol == "BTC/USDT"


# ---------- KIS market data delegates KRX validation to symbol_normalizer(LA-7) ----------


async def test_kis_get_ticker_valid_krx_code_passes_through():
    def handler(request: httpx.Request) -> httpx.Response:
        if "inquire-price" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "ok",
                    "output": {"stck_prpr": "70000", "acml_vol": "1"},
                },
            )
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output1": {"askp1": "70100", "bidp1": "69900"}}
        )

    adapter = _make_kis_adapter(handler)
    ticker = await adapter.get_ticker("005930")

    assert ticker.symbol == "005930"


async def test_kis_get_ticker_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_ticker("BTC/USDT")


async def test_kis_get_orderbook_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_orderbook("AAPL")


async def test_kis_get_ohlcv_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_ohlcv("12345", "1d")
