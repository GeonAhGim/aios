"""02c_bitget_api_v2_extended_spec_v1.md §1.6 통합테스트 — Tax(세금 신고용 데이터).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler, *, sleep_fn=None) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=True, http_client=client, sleep_fn=sleep_fn
    )


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
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

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


async def test_get_spot_tax_records_raises_retryable_on_api_error():
    """불변식 위반(00000이 아닌 body code)은 성공으로 위장하지 않고 재시도
    가능 예외로 전파돼야 한다(test_bitget_retry.py::
    test_api_error_response_raises_retryable_by_default와 동일 계약)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"code": "99999", "msg": "internal error", "requestTime": 1, "data": {}}
        )

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_spot_tax_records()


async def test_get_futures_tax_records_raises_fatal_on_auth_error():
    """서명 오류(40012)는 재시도해도 성공할 수 없으므로 즉시 Fatal로
    전파돼야 한다(error_codes.py::_AUTH_CODES)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"code": "40012", "msg": "invalid sign", "requestTime": 1, "data": {}}
        )

    adapter = _make_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_futures_tax_records()


async def test_get_margin_tax_records_raises_retryable_on_non_json_response():
    """거래소가 비JSON 본문을 반환하는 장애를 주입 — UNKNOWN_RESPONSE는
    retryable=True override 계약(adapter.py::_classify_body 주석 참고)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_margin_tax_records()


async def test_get_p2p_tax_records_raises_retryable_after_network_failure_injection():
    """네트워크 계층 실패주입 — 매 시도마다 httpx.ConnectError가 발생하면
    재시도 예산(max_attempts=4) 소진 후 RetryableExchangeError로 전파돼야
    한다(transport.py::_request_with_retry TRANSIENT_NETWORK 분류)."""

    calls = {"n": 0}

    async def fake_sleep(seconds: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    adapter = _make_adapter(handler, sleep_fn=fake_sleep)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_p2p_tax_records()

    assert calls["n"] == 4
