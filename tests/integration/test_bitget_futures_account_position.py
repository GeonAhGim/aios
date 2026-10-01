"""02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0 (Account/Position).

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.

task-10257 DEEPEN — negative test 0건이던 상태에서 입력 불변식 위반
(알 수 없는 position_mode)·거래소 비즈니스 오류 코드(FatalExchangeError)·
네트워크 장애 재시도 소진(RetryableExchangeError) 경로를 추가한다.
"""

import json
import time
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from tests.integration.bitget_futures_doubles import json_response, make_adapter


async def _instant_sleep(_seconds: float) -> None:
    """실패주입 테스트용 — 재시도 백오프를 기다리지 않고 재시도 소진
    경로만 검증한다(bitget_futures_doubles._instant_sleep과 동일 원칙)."""


async def test_get_futures_accounts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/accounts"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "marginCoin": "usdt",
                        "available": "1000",
                        "accountEquity": "1200",
                        "locked": "0",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    balances = await adapter.get_futures_accounts()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("1200")


async def test_set_futures_leverage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-leverage"
        body = json.loads(request.content)
        assert body["leverage"] == "5"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    await adapter.set_futures_leverage("BTC/USDT", Decimal("5"))


async def test_set_futures_margin_mode_rejects_invalid_value():
    adapter = make_adapter(lambda request: json_response({"code": "00000", "data": {}}))
    try:
        await adapter.set_futures_margin_mode("BTC/USDT", "bogus")
        raise AssertionError("ValueError를 던졌어야 함")
    except ValueError:
        pass


async def test_get_futures_liquidation_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/liq-price"
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"liqPx": "70000"}}
        )

    adapter = make_adapter(handler)
    price = await adapter.get_futures_liquidation_price("BTC/USDT")

    assert price == Decimal("70000")


async def test_get_futures_position_returns_none_when_empty():
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )
    )
    assert await adapter.get_futures_position("BTC/USDT") is None


async def test_get_futures_position_parses_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/single-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "total": "0.5",
                        "openPriceAvg": "80000",
                        "markPrice": "81000",
                        "unrealizedPL": "500",
                        "achievedProfits": "0",
                        "leverage": "10",
                        "marginSize": "4000",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    position = await adapter.get_futures_position("BTC/USDT")

    assert position is not None
    assert position.quantity == Decimal("0.5")
    assert position.leverage == Decimal("10")


async def test_get_futures_positions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/all-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "total": "0.5", "openPriceAvg": "80000"}],
            }
        )

    adapter = make_adapter(handler)
    positions = await adapter.get_futures_positions()

    assert positions[0].symbol == "BTCUSDT"


async def test_get_futures_account_single():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/account"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"marginCoin": "USDT", "available": "1000", "locked": "0"},
            }
        )

    adapter = make_adapter(handler)
    balance = await adapter.get_futures_account("BTC/USDT")

    assert balance.asset == "USDT"
    assert balance.available == Decimal("1000")


async def test_set_futures_margin_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-margin"
        body = json.loads(request.content)
        assert body["amount"] == "50"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    await adapter.set_futures_margin("BTC/USDT", Decimal("50"))


async def test_get_futures_max_open_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/max-open"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"maxOpenAvailable": "10"},
            }
        )

    adapter = make_adapter(handler)
    amount = await adapter.get_futures_max_open_amount("BTC/USDT")

    assert amount == Decimal("10")


async def test_get_futures_account_bills_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/bill"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"billId": "b-1", "amount": "10"}],
            }
        )

    adapter = make_adapter(handler)
    bills = await adapter.get_futures_account_bills()

    assert bills == [{"billId": "b-1", "amount": "10"}]


async def test_get_futures_position_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/history-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "netProfit": "12.5"}],
            }
        )

    adapter = make_adapter(handler)
    history = await adapter.get_futures_position_history(symbol="BTC/USDT")

    assert history == [{"symbol": "BTCUSDT", "netProfit": "12.5"}]


async def test_set_futures_position_mode_rejects_invalid_value():
    adapter = make_adapter(lambda request: json_response({"code": "00000", "data": {}}))
    with pytest.raises(ValueError):
        await adapter.set_futures_position_mode("bogus")


async def test_get_futures_accounts_raises_fatal_on_auth_boundary_error_code():
    """불변식 위반 — Bitget이 40099("exchange environment is incorrect",
    task-2514 live-key measurement)로 응답하면 자격증명이 애초에 이 API
    표면에 닿을 수 없다는 뜻이라 재시도해도 결과가 바뀌지 않는다
    (error_codes.py AUTH 분류, retryable=False → FatalExchangeError)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "40099",
                "msg": "exchange environment is incorrect",
                "requestTime": 1,
                "data": {},
            }
        )

    adapter = make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_futures_accounts()


async def test_get_futures_liquidation_price_raises_fatal_on_auth_boundary_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "40085",
                "msg": "Classic Account API not supported",
                "requestTime": 1,
                "data": {},
            }
        )

    adapter = make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_futures_liquidation_price("BTC/USDT")


async def test_get_futures_position_raises_fatal_on_auth_boundary_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"code": "40012", "msg": "signature error", "requestTime": 1, "data": {}}
        )

    adapter = make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_futures_position("BTC/USDT")


async def test_get_futures_accounts_retries_exhausted_raises_retryable_on_connect_error():
    """실패주입 — handler가 매 시도마다 httpx.ConnectError를 던져 네트워크
    장애를 재현한다. ResilientTransport가 max_attempts(4)까지 재시도한 뒤
    TRANSIENT_NETWORK(retryable=True)로 분류해 RetryableExchangeError를
    올려야 한다(src/exchanges/common/transport.py _request_with_retry)."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler, sleep_fn=_instant_sleep)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_futures_accounts()

    assert attempts == 4


@pytest.mark.perf
async def test_get_futures_positions_latency_budget():
    """성능 단언 — 모킹된 전송 경로에서 순차 호출 20회의 p95 지연이
    예산(500ms/call) 안에 들어오는지 확인한다. 실거래소 왕복이 아니라
    어댑터 직렬화/서명/파싱 오버헤드 회귀를 잡는 용도라 예산을 넉넉히
    둔다(task-10254 teaching: 공유 CI 환경에서 과도하게 좁은 예산은
    red-flake를 유발)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "total": "0.5", "openPriceAvg": "80000"}],
            }
        )

    adapter = make_adapter(handler)

    durations: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        await adapter.get_futures_positions()
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.5
