"""02c_bitget_api_v2_extended_spec_v1.md §1.3 통합테스트 — P2P.

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).

task-9391 DEEPEN(원 리프 task-6704, 고아 산출물 회수 5828 qa-2) — 성공 경로 4건만
있고 negative test 0건이었던 것을, `_request` 계약에 맞춰
불변식 위반 3건 + 실패주입 1건으로 보강한다.
"""

from collections.abc import Awaitable, Callable

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(
    handler,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=True, http_client=client, sleep_fn=sleep_fn
    )


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_p2p_ads_filters_by_coin():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/advList"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"advId": "a-1", "coin": "USDT"}],
            }
        )

    adapter = _make_adapter(handler)
    ads = await adapter.get_p2p_ads(coin="usdt")

    assert ads == [{"advId": "a-1", "coin": "USDT"}]


async def test_get_p2p_merchant_info_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/merchantInfo"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"merchantId": "m-1", "nickname": "trader1"},
            }
        )

    adapter = _make_adapter(handler)
    info = await adapter.get_p2p_merchant_info()

    assert info == {"merchantId": "m-1", "nickname": "trader1"}


async def test_get_p2p_orders_filters_by_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/orderList"
        assert request.url.params["status"] == "completed"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "o-1", "status": "completed"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_p2p_orders(status="completed")

    assert orders == [{"orderId": "o-1", "status": "completed"}]


async def test_get_p2p_merchants_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/merchantList"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"merchantId": "m-1"}],
            }
        )

    adapter = _make_adapter(handler)
    merchants = await adapter.get_p2p_merchants()

    assert merchants == [{"merchantId": "m-1"}]


# --- no-op sleep for retry tests (백오프 대기 생략) --------------------------------


async def _no_sleep(_seconds: float) -> None:
    """재시도 백오프(최대 1+2+4=7s)를 실제로 기다리지 않기 위한 대역."""


# --- negative tests (불변식 위반 입력 거부) -------------------------------------


async def test_get_p2p_ads_raises_fatal_on_permission_denied():
    """P2P 광고 조회에 권한 없는 응답(code != 00000)을 보내면,
    빈 목록으로 눙치지 않고 FatalExchangeError로 즉시 실패해야 한다 —
    fail-closed 기본값."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "40099", "msg": "auth failed"})

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_p2p_ads(coin="usdt")


async def test_get_p2p_ads_raises_on_missing_data_field():
    """venue가 code: 00000(성공)이면서 스키마가 바뀌어 data 필드를 빼먹은
    응답을 보내면, `list(raw["data"])`가 빈 목록으로 눙치지 않고
    KeyError로 즉시 실패해야 한다 — 광고 0건과 파싱 실패를 구분해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1})

    adapter = _make_adapter(handler)
    with pytest.raises(KeyError):
        await adapter.get_p2p_ads(coin="usdt")


async def test_get_p2p_orders_raises_retryable_on_unknown_error():
    """분류되지 않은 오류코드는 fail-closed 기본값(SERVER_ERROR, 재시도 가능)으로
    처리된다 — P2P 주문 내역을 조회하지 못했는데 빈 목록을 반환해 "주문 없음"과
    "일시적 서버 오류"를 뒤섞으면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "internal error"})

    adapter = _make_adapter(handler, sleep_fn=_no_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_p2p_orders(status="completed")


# --- 실패주입 (의존성 예외 전파) --------------------------------------------------


async def test_get_p2p_merchant_info_propagates_transport_failure():
    """P2P merchant 정보 조회 도중 커넥션 자체가 끊기면(httpx.TransportError),
    그 예외를 삼켜 "merchant 정보 없음"으로 위장하지 않고 RetryableExchangeError로
    전파해야 한다 — 재시도를 모두 소진한 뒤에도 실패하면 호출자가 알 수 있어야
    한다(fail-closed)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injected connection failure", request=request)

    adapter = _make_adapter(handler, sleep_fn=_no_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_p2p_merchant_info()
