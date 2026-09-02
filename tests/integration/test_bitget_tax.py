"""02c_bitget_api_v2_extended_spec_v1.md §1.6 통합테스트 — Tax(세금 신고용 데이터).

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


async def test_get_spot_tax_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tax/spot-record"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "amount": "0.01"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_spot_tax_records()

    assert records == [{"symbol": "BTCUSDT", "amount": "0.01"}]


async def test_get_futures_tax_records_sends_time_range():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tax/future-record"
        assert request.url.params["startTime"] == "1700000000000"
        assert request.url.params["endTime"] == "1700001000000"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_futures_tax_records(
        start_time="1700000000000", end_time="1700001000000"
    )

    assert records == []


async def test_get_margin_tax_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tax/margin-record"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "interest": "0.001"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_margin_tax_records()

    assert records == [{"symbol": "BTCUSDT", "interest": "0.001"}]


async def test_get_p2p_tax_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tax/p2p-record"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "p-1", "amount": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_p2p_tax_records()

    assert records == [{"orderId": "p-1", "amount": "100"}]
