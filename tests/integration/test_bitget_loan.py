"""02c_bitget_api_v2_extended_spec_v1.md §1.5 통합테스트 — Loan(코인담보대출).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""
import json
from decimal import Decimal

import httpx

from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_loan_coin_info_filters_by_coin():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/coin-info"
        assert request.url.params["coin"] == "BTC"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "BTC", "maxLtv": "0.7"}],
            }
        )

    adapter = _make_adapter(handler)
    info = await adapter.get_loan_coin_info(coin="btc")

    assert info == [{"coin": "BTC", "maxLtv": "0.7"}]


async def test_get_loan_hourly_interest_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/hourly-interest-rate"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "USDT", "hourlyInterestRate": "0.00001"}],
            }
        )

    adapter = _make_adapter(handler)
    rates = await adapter.get_loan_hourly_interest_rate("usdt")

    assert rates == [{"coin": "USDT", "hourlyInterestRate": "0.00001"}]


async def test_borrow_loan_sends_pledge():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/borrow"
        body = json.loads(request.content)
        assert body == {"loanCoin": "USDT", "pledgeCoin": "BTC", "pledgeAmount": "0.5"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "l-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.borrow_loan("usdt", "btc", Decimal("0.5"))

    assert result == {"orderId": "l-1"}


async def test_repay_loan_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/repay"
        body = json.loads(request.content)
        assert body == {"orderId": "l-1", "amount": "100"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "l-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.repay_loan("l-1", Decimal("100"))

    assert result == {"orderId": "l-1"}


async def test_revise_loan_pledge_sends_revise_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/revise-pledge"
        body = json.loads(request.content)
        assert body == {"orderId": "l-1", "amount": "0.1", "reviseType": "IN"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "l-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.revise_loan_pledge("l-1", Decimal("0.1"))

    assert result == {"orderId": "l-1"}


async def test_get_ongoing_loans_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/ongoing-orders"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "l-1", "status": "ongoing"}],
            }
        )

    adapter = _make_adapter(handler)
    loans = await adapter.get_ongoing_loans()

    assert loans == [{"orderId": "l-1", "status": "ongoing"}]


async def test_get_loan_repay_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/repay-history"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "l-1", "amount": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_loan_repay_history()

    assert history == [{"orderId": "l-1", "amount": "100"}]


async def test_get_loan_liquidation_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/loan/liquidation-records"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "l-1", "liquidatedAmount": "0.5"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_loan_liquidation_records()

    assert records == [{"orderId": "l-1", "liquidatedAmount": "0.5"}]
