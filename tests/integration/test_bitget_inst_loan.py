"""02c_bitget_api_v2_extended_spec_v1.md §1.11 통합테스트 — Inst Loan(기관 전용 대출).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""
import httpx

from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_inst_loan_products_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/ins-loan/product-infos"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"productId": "il-1"}],
            }
        )

    adapter = _make_adapter(handler)
    products = await adapter.get_inst_loan_products()

    assert products == [{"productId": "il-1"}]


async def test_get_inst_loan_ensure_coins_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/ins-loan/ensure-coins-convert"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "BTC", "convertRate": "0.7"}],
            }
        )

    adapter = _make_adapter(handler)
    coins = await adapter.get_inst_loan_ensure_coins()

    assert coins == [{"coin": "BTC", "convertRate": "0.7"}]


async def test_get_inst_loan_orders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/ins-loan/loan-order"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "il-order-1", "ltv": "0.5"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_inst_loan_orders()

    assert orders == [{"orderId": "il-order-1", "ltv": "0.5"}]


async def test_get_inst_loan_repaid_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/ins-loan/repaid-history"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "il-order-1", "repaidAmount": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_inst_loan_repaid_history()

    assert history == [{"orderId": "il-order-1", "repaidAmount": "100"}]
