"""Bitget v2 실제 라이브 응답(2026-08-28, GET /api/v2/spot/market/orderbook?symbol=BTCUSDT&limit=5)
캡처본을 fixture로 사용."""
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError
from src.core.parser.orderbook_parser import parse_orderbook

REAL_BITGET_ORDERBOOK = {
    "asks": [
        ["80565", "1.3861510000000000"],
        ["80568.63", "0.0297160000000000"],
    ],
    "bids": [
        ["80564.99", "0.3894280000000000"],
        ["80562.93", "0.0001860000000000"],
    ],
    "ts": "1787853071676",
}


def test_parse_orderbook_from_real_bitget_response():
    book = parse_orderbook(REAL_BITGET_ORDERBOOK, "bitget", "BTC/USDT")
    assert book.symbol == "BTC/USDT"
    assert book.asks[0].price == Decimal("80565")
    assert book.asks[0].quantity == Decimal("1.3861510000000000")
    assert book.bids[0].price == Decimal("80564.99")
    assert book.bids[0].price > book.bids[1].price  # 내림차순(최우선 매수호가가 첫 행)
    assert book.asks[0].price < book.asks[1].price  # 오름차순(최우선 매도호가가 첫 행)


def test_parse_orderbook_missing_field_raises():
    broken = {"asks": REAL_BITGET_ORDERBOOK["asks"], "bids": REAL_BITGET_ORDERBOOK["bids"]}
    with pytest.raises(FatalExchangeError):
        parse_orderbook(broken, "bitget", "BTC/USDT")


def test_parse_orderbook_unsupported_exchange_raises():
    with pytest.raises(FatalExchangeError):
        parse_orderbook(REAL_BITGET_ORDERBOOK, "kis", "BTC/USDT")
