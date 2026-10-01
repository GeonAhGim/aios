"""NHAdapter 통합 테스트 — 토큰 발급·요청 레벨 에러처리·기타·health check.

실제 NH API 키가 없는 상태라 httpx.MockTransport로 공식 REST 요청 형태를
재현해 검증한다(test_kis_adapter.py와 동일 원칙). market_data/account/
trading의 요청·응답 필드명은 2026-09-03(task-114) 공식 OpenAPI 스펙
(`https://www.nhplug.com/openapi-docs/krstock/openapi.json`, 도메인이
정본임을 `nhplug-sdk` 레포 `docs/README.md`가 명시)을 직접 내려받아
확인한 값이다 — 02e_nh_api_spec_v1.md §3 참조.

RATCHET-split(task-10196, ADR-2026-09-10-C LOC 규율): 원래 단일 파일이던
이 테스트를 책임별로 분할했다 — 공유 어댑터/라우팅 헬퍼는
`_nh_adapter_helpers.py`, market/account 조회는
`test_nh_adapter_market_data.py`, 주문(place/cancel/modify/get_order)은
`test_nh_adapter_trading.py`, WebSocket 구독(task-2615, mc 채널 포함)은
`test_nh_adapter_websocket.py`에 둔다. 공개 API(각 테스트 함수 이름)는
변경하지 않았다.
"""

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from tests.integration._nh_adapter_helpers import TOKEN_RESPONSE, _make_adapter, _route, _success

# ---------- token issuance ----------


async def test_ensure_token_sends_form_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        assert request.url.params["appkey"] == "appkey"
        assert request.url.params["appsecretkey"] == "appsecret"
        assert request.url.params["grant_type"] == "client_credentials"
        return httpx.Response(200, json=TOKEN_RESPONSE)

    adapter = _make_adapter(handler)
    token = await adapter._ensure_token()

    assert token == "tok-1"


async def test_ensure_token_raises_fatal_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter._ensure_token()


# ---------- request-level error handling ----------


async def test_request_raises_retryable_on_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {"/krstock/quote/v1/currentPrice": lambda r: httpx.Response(200, text="<html/>")},
        )

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_raises_retryable_on_business_failure_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200, json={"rsp_cd": "99999", "rsp_msg": "실패"}
                )
            },
        )

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_treats_wanryo_message_as_success_even_with_unlisted_code():
    """SDK 관례 — rsp_cd가 알려진 성공코드 목록에 없어도 rsp_msg에
    "완료"가 포함되면 성공으로 취급한다(공식 nhplug/client.py::is_success()
    확인)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200,
                    json={
                        "rsp_cd": "ZZZZZ",
                        "rsp_msg": "처리 완료",
                        "Output_0": {"stck_prpr": "70000", "bidp": "69900", "askp": "70100"},
                    },
                )
            },
        )

    adapter = _make_adapter(handler)
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")


# ---------- misc ----------


async def test_is_paper_trading_and_sandboxed_are_always_false():
    """task-106 재확인 — 모의투자 도메인이 공식 문서상 "미제공"이라
    확인되기 전까지 항상 False(생성자 플래그와 무관, adapter.py 참조).
    Executor의 이중 검사(mode!=PAPER + 이 두 프로퍼티)가 이 신호로
    이 adapter의 실거래를 차단하는 것이 의도된 동작이다."""
    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )
    assert adapter.is_paper_trading is False
    assert adapter.is_sandboxed is False


# ---------- health check ----------


async def test_health_check_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"dca": "0"}, Output_1=[]))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is True


async def test_health_check_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "실패"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is False
