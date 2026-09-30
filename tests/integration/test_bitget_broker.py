"""02c_bitget_api_v2_extended_spec_v1.md §1.7 통합테스트 — Broker(브로커/리셀러).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.exceptions import (
    FatalExchangeError,
    FrozenZonePaperAdapterBlockedError,
    RetryableExchangeError,
)
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_broker_info_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/info"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"brokerId": "b-1"},
            }
        )

    adapter = _make_adapter(handler)
    info = await adapter.get_broker_info()

    assert info == {"brokerId": "b-1"}


async def test_get_broker_subaccounts_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/subaccount-list"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"subAccountList": [{"subAccountUid": "u-1"}]},
            }
        )

    adapter = _make_adapter(handler)
    subaccounts = await adapter.get_broker_subaccounts()

    assert subaccounts == [{"subAccountUid": "u-1"}]


async def test_create_broker_subaccount_sends_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/create-subaccount"
        body = json.loads(request.content)
        assert body == {"subAccountName": "desk-a"}
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"subAccountUid": "u-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.create_broker_subaccount("desk-a")

    assert result == {"subAccountUid": "u-1"}


async def test_create_broker_subaccount_apikey_sends_permissions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/create-subaccount-apikey"
        body = json.loads(request.content)
        assert body["permType"] == "read,trade"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"apikey": "k-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.create_broker_subaccount_apikey(
        "u-1", "passphrase123", permissions=["read", "trade"]
    )

    assert result == {"apikey": "k-1"}


async def test_get_broker_subaccount_assets_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/subaccount-assets"
        assert request.url.params["subAccountUid"] == "u-1"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "USDT", "available": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    assets = await adapter.get_broker_subaccount_assets("u-1")

    assert assets == [{"coin": "USDT", "available": "100"}]


async def test_transfer_broker_subaccount_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/subaccount-transfer"
        body = json.loads(request.content)
        assert body["amount"] == "25"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    result = await adapter.transfer_broker_subaccount("u-1", "usdt", Decimal("25"))

    assert result is True


async def test_get_broker_rebate_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/broker/account/subaccount-deposit"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"rebateAmount": "1.5"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_broker_rebate_records()

    assert records == [{"rebateAmount": "1.5"}]


# ── Negative tests ──────────────────────────────────────────────────────────


async def test_get_broker_info_raises_fatal_on_auth_error_code():
    """40012 서명 오류 → FatalExchangeError(retryable=False).

    INVARIANTS I-05 — 인증/권한 오류는 재시도 없이 즉시 차단.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"code": "40012", "msg": "signature error", "requestTime": 1, "data": None}
        )

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError, match="40012"):
        await adapter.get_broker_info()


async def test_get_broker_subaccounts_raises_retryable_on_server_error_code():
    """99999 미지 코드 → RetryableExchangeError(기본 SERVER_ERROR).

    error_codes.py — "00000이 아니면 일단 재시도 가능" 기존 계약 유지.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "unknown", "requestTime": 1, "data": None})

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError, match="99999"):
        await adapter.get_broker_subaccounts()


async def test_transfer_broker_subaccount_blocks_live_adapter():
    """demo_mode=False adapter → FrozenZonePaperAdapterBlockedError.

    broker_mixin.py::transfer_broker_subaccount @require_paper_sandbox 데코레이터 검증.
    """
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=AsyncMock()
    )
    # 데코레이터가 self.is_paper_trading/is_sandboxed를 확인하므로
    # _request가 호출되지 않아도 예외가 발생해야 함
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.transfer_broker_subaccount("u-1", "usdt", Decimal("10"))


# ── Failure-injection test ──────────────────────────────────────────────────


async def test_get_broker_info_propagates_transport_exception():
    """HTTP 전송 계층 예외 → ResilientTransport가 RetryableExchangeError로 변환해 전파.

    task-4084 실패주입 — 연결 타임아웃은 재시도 후에도 실패하면
    RetryableExchangeError로 감싸져 호출자에게 전달된다(adapter.py::_request).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection refused")

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError, match="connection refused"):
        await adapter.get_broker_info()
