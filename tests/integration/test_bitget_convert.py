"""02c_bitget_api_v2_extended_spec_v1.md §1.1 통합테스트 — Convert(간편환전).

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_convert_currencies_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/convert/currencies"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "USDT"}, {"coin": "BTC"}],
            }
        )

    adapter = _make_adapter(handler)
    currencies = await adapter.get_convert_currencies()

    assert currencies == [{"coin": "USDT"}, {"coin": "BTC"}]


async def test_get_convert_quote_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/convert/quoted-price"
        assert request.url.params["fromCoin"] == "USDT"
        assert request.url.params["toCoin"] == "BTC"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"traceId": "t-1", "toCoinSize": "0.001"},
            }
        )

    adapter = _make_adapter(handler)
    quote = await adapter.get_convert_quote("usdt", "btc", Decimal("100"))

    assert quote == {"traceId": "t-1", "toCoinSize": "0.001"}


async def test_execute_convert_sends_trace_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/convert/trade"
        body = json.loads(request.content)
        assert body["traceId"] == "t-1"
        assert body["fromCoinSize"] == "100"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"cnvtId": "c-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.execute_convert(
        "t-1", "usdt", "btc", Decimal("100"), Decimal("0.001")
    )

    assert result == {"cnvtId": "c-1"}


async def test_execute_convert_blocked_on_live_configured_adapter():
    """레드팀 #2026-09-02-32 회귀 테스트 — Executor를 거치지 않는 이
    메서드도 LIVE(demo_mode=False)로 구성된 adapter에서는 거래소 호출
    자체가 막혀야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=client
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.execute_convert(
            "t-1", "usdt", "btc", Decimal("100"), Decimal("0.001")
        )


async def test_execute_convert_rejects_non_positive_amount():
    """레드팀 #2026-09-02-33 회귀 테스트 — 금액 sanity check."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("검증에 걸렸어야 할 요청이 실제로 나갔습니다.")

    adapter = _make_adapter(handler)
    with pytest.raises(ValueError):
        await adapter.execute_convert("t-1", "usdt", "btc", Decimal("0"), Decimal("0.001"))


async def test_get_convert_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/convert/convert-record"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"cnvtId": "c-1", "fromCoin": "USDT", "toCoin": "BTC"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_convert_history()

    assert history == [{"cnvtId": "c-1", "fromCoin": "USDT", "toCoin": "BTC"}]
