"""02c_bitget_api_v2_extended_spec_v1.md §1.2 통합테스트 — Subaccount(서브계정).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
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


async def test_get_subaccounts_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user/virtual-subaccount-list"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"subAccountList": [{"subAccountUid": "u-1"}]},
            }
        )

    adapter = _make_adapter(handler)
    subaccounts = await adapter.get_subaccounts()

    assert subaccounts == [{"subAccountUid": "u-1"}]


async def test_create_subaccount_sends_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user/create-virtual-subaccount"
        body = json.loads(request.content)
        assert body == {"subAccountName": "strategy-a"}
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"subAccountUid": "u-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.create_subaccount("strategy-a")

    assert result == {"subAccountUid": "u-1"}


async def test_create_subaccount_apikey_sends_permissions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user/create-virtual-subaccount-apikey"
        body = json.loads(request.content)
        assert body["permType"] == "read,trade"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"apikey": "k-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.create_subaccount_apikey(
        "u-1", "passphrase123", permissions=["read", "trade"]
    )

    assert result == {"apikey": "k-1"}


async def test_create_subaccount_apikey_defaults_to_read_only_permission():
    """레드팀 #2026-09-02-34 회귀 테스트 — permissions 생략 시 거래소의
    미지정 기본값에 맡기지 않고 명시적으로 read-only를 보낸다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user/create-virtual-subaccount-apikey"
        body = json.loads(request.content)
        assert body["permType"] == "read"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"apikey": "k-2"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.create_subaccount_apikey("u-1", "passphrase123")

    assert result == {"apikey": "k-2"}


async def test_get_subaccount_apikeys_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/user/virtual-subaccount-apikey-list"
        assert request.url.params["subAccountUid"] == "u-1"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"apikey": "k-1"}],
            }
        )

    adapter = _make_adapter(handler)
    keys = await adapter.get_subaccount_apikeys("u-1")

    assert keys == [{"apikey": "k-1"}]


async def test_get_subaccount_assets_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/account/sub-account-assets"
        assert request.url.params["subUid"] == "u-1"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "USDT", "available": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    assets = await adapter.get_subaccount_assets("u-1")

    assert assets == [{"coin": "USDT", "available": "100"}]


async def test_transfer_to_subaccount_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/subaccount-transfer"
        body = json.loads(request.content)
        assert body["subAccountUid"] == "u-1"
        assert body["amount"] == "50"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    result = await adapter.transfer_to_subaccount("u-1", "usdt", Decimal("50"))

    assert result is True


async def test_transfer_to_subaccount_blocked_on_live_configured_adapter():
    """레드팀 #2026-09-02-32 회귀 테스트."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=client
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.transfer_to_subaccount("u-1", "usdt", Decimal("50"))
