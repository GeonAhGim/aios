"""02c_bitget_api_v2_extended_spec_v1.md §1.11 통합테스트 — Inst Loan(기관 전용 대출).

httpx.MockTransport 기반 검증(test_bitget_retry.py와 동일 원칙).

task-9389 DEEPEN(원 리프 task-6704, 고아 산출물 회수 5828 qa-2) — 성공 경로 4건만
있고 negative test 0건이었던 것을, `inst_loan_mixin.py`가 공유하는 `_request` 계약
(§1.11 docstring: 기관 전용 → 권한 오류가 정상 응답 케이스, 8.3 원칙)에 맞춰
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


async def _no_sleep(_seconds: float) -> None:
    """재시도 백오프(최대 1+2+4=7s)를 실제로 기다리지 않기 위한 대역
    (test_bitget_retry.py의 `sleep_fn` 관례와 동일)."""


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


# --- negative tests (불변식 위반 입력 거부) -------------------------------------


async def test_get_inst_loan_products_raises_fatal_on_permission_denied():
    """§1.11 docstring — 기관 전용 엔드포인트에 리테일 키로 접근하면 권한 오류가
    정상적인 응답 케이스다(8.3 원칙). AUTH 코드(40099)는 재시도해도 같은 방식으로
    실패하므로(error_codes.py), 조용히 빈 목록을 반환하지 않고 즉시
    FatalExchangeError로 실패해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "40099", "msg": "exchange environment is incorrect"})

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_inst_loan_products()


async def test_get_inst_loan_ensure_coins_raises_retryable_on_server_error():
    """분류되지 않은 오류코드는 fail-closed 기본값(SERVER_ERROR, 재시도 가능)으로
    처리된다(error_codes.py) — 담보 코인 환산율을 조회하지 못했는데 빈 목록을
    반환해 "담보 코인 없음"과 "일시적 서버 오류"를 뒤섞으면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "internal error"})

    adapter = _make_adapter(handler, sleep_fn=_no_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_inst_loan_ensure_coins()


async def test_get_inst_loan_orders_raises_on_missing_data_field():
    """venue가 `code: 00000`(성공)이면서 스키마가 바뀌어 `data` 필드를 빼먹은
    응답을 보내면, `list(raw["data"])`가 빈 목록으로 눙치지 않고 KeyError로
    즉시 실패해야 한다 — 진행중 대출 0건과 파싱 실패를 구분해야 하는 LTV
    모니터링 축(FROZEN_PAPER_ONLY 리스크 경로가 이 값을 읽는다)의 요구."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1})

    adapter = _make_adapter(handler)
    with pytest.raises(KeyError):
        await adapter.get_inst_loan_orders()


# --- 실패주입 (의존성 예외 전파) --------------------------------------------------


async def test_get_inst_loan_repaid_history_propagates_transport_failure():
    """기관 대출 상환 이력 조회 도중 커넥션 자체가 끊기면(httpx.TransportError),
    그 예외를 삼켜 "상환 이력 없음"으로 위장하지 않고 RetryableExchangeError로
    전파해야 한다 — 재시도를 모두 소진한 뒤에도 실패하면 호출자가 알 수 있어야
    한다(fail-closed)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injected connection failure", request=request)

    adapter = _make_adapter(handler, sleep_fn=_no_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_inst_loan_repaid_history()
