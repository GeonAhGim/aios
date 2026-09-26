"""task-7600(BR-22b) -- BinanceAdapter assembly + BR-9 factory registration
+ signed/public/user-stream request routing + 429/418 backoff tests.

D2 floor: negative >=3, failure-injection 1, numeric perf assertion 1.
D3 (INVARIANTS I-02/I-03 대조 적대적 테스트): LIVE 하드가드가 BR-9 확장점
뒤에서도 그대로 적용됨을 증명(`test_factory_spi_extension.py`와 동일
원칙). replay_verify: N/A(이 leaf는 어댑터 조립/HTTP 라우팅만 검증하고
DB/이벤트스토어에 쓰지 않음).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

import src.exchanges.binance.factory as binance_factory_module
from src.core.exceptions import (
    FatalExchangeError,
    FrozenZonePaperAdapterBlockedError,
    RetryableExchangeError,
)
from src.exchanges.binance.factory import BinanceAdapter
from src.exchanges.binance.venue_profile import BINANCE_SPOT_PROFILE
from src.exchanges.factory import build_adapter, register_exchange_adapter_factory


@pytest.fixture(autouse=True)
def _ensure_binance_registered() -> None:
    """`test_factory_spi_extension.py`'s autouse fixture resets the shared
    BR-9 registry for its own tests -- re-registering here on every test
    makes this file's assertions independent of module-import/test-run
    order (register_exchange_adapter_factory is idempotent, last call
    wins, `src/exchanges/factory.py` module docstring)."""
    register_exchange_adapter_factory("binance", binance_factory_module._binance_adapter_factory)


def _mock_client(
    handler: Any, *, base_url: str = "https://testnet.binance.vision"
) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))


_TICKER_JSON = {"lastPrice": "1", "bidPrice": "1", "askPrice": "1", "volume": "1"}


def _fake_sleep_recorder() -> tuple[list[float], Any]:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    return delays, fake_sleep


# ---- BR-9 등록 (DoD #4 -- factory.py if-분기 불변) ----


def test_build_adapter_resolves_binance_via_br9_extension_point():
    adapter = build_adapter("binance", "key", "secret", None)
    assert isinstance(adapter, BinanceAdapter)
    assert adapter.is_paper_trading is True


def test_build_adapter_binance_still_blocked_by_live_guard_without_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """D3 대조 -- BR-9 확장점도 LIVE 하드가드 *뒤*에 있다(우회 경로가
    아님, ADDING_AN_EXCHANGE.md §3)."""
    monkeypatch.delenv("AIOS_ALLOW_LIVE_ADAPTER", raising=False)
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter("binance", "key", "secret", None, demo_mode=False)


# ---- venue_profile/capabilities 배선 (DoD #3) ----


def test_venue_profile_returns_the_wired_constant():
    adapter = BinanceAdapter("key", "secret")
    assert adapter.venue_profile() is BINANCE_SPOT_PROFILE


def test_get_capabilities_declares_spot_only_with_websocket():
    adapter = BinanceAdapter("key", "secret")
    caps = adapter.get_capabilities()
    assert caps.exchange_name == "binance"
    assert caps.supports_spot is True
    assert caps.supports_futures is False
    assert caps.supports_websocket is True
    assert caps.max_leverage == Decimal("1")


def test_is_paper_trading_and_is_sandboxed_expose_constructor_flag():
    paper = BinanceAdapter("key", "secret", demo_mode=True)
    assert paper.is_paper_trading is True
    assert paper.is_sandboxed is True


# ---- 요청 라우팅: public/user-stream/signed (endpoint security type) ----


async def test_public_path_sends_no_api_key_header_or_signature():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-MBX-APIKEY" not in request.headers
        assert "signature" not in str(request.url)
        return httpx.Response(200, json=_TICKER_JSON)

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    await adapter.get_ticker("BTCUSDT")


async def test_user_stream_path_sends_api_key_header_but_no_signature():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-MBX-APIKEY") == "key"
        assert "signature" not in str(request.url)
        return httpx.Response(200, json={"listenKey": "abc"})

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    listen_key = await adapter._get_listen_key()
    assert listen_key == "abc"


async def test_signed_path_sends_api_key_header_and_signature():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-MBX-APIKEY") == "key"
        query = str(request.url.query, "utf-8")
        assert "signature=" in query
        assert "timestamp=" in query
        assert "recvWindow=" in query
        return httpx.Response(200, json={"balances": []})

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    await adapter.get_balance()


# ---- 429/418 백오프 (D2 floor: 장애주입 + 부정 테스트) ----


async def test_429_then_success_retries_with_backoff():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"msg": "rate limited"})
        return httpx.Response(200, json=_TICKER_JSON)

    delays, fake_sleep = _fake_sleep_recorder()
    adapter = BinanceAdapter(
        "key", "secret", http_client=_mock_client(handler), sleep_fn=fake_sleep
    )
    ticker = await adapter.get_ticker("BTCUSDT")
    assert calls["n"] == 2
    assert ticker.price == Decimal("1")
    assert delays == [0.0]  # Retry-After 헤더 값을 그대로 따름


async def test_418_ip_ban_then_success_retries_with_backoff():
    """장애주입: Binance 고유 418(IP auto-ban) -- 공통 error_taxonomy에는
    없는 상태코드가 실제로 재시도 경로를 타는지 종단 검증."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(418, headers={"Retry-After": "0"}, json={"msg": "banned"})
        return httpx.Response(200, json={"balances": []})

    delays, fake_sleep = _fake_sleep_recorder()
    adapter = BinanceAdapter(
        "key", "secret", http_client=_mock_client(handler), sleep_fn=fake_sleep
    )
    balances = await adapter.get_balance()
    assert calls["n"] == 2
    assert balances == []
    assert delays == [0.0]


async def test_429_exhausts_retries_raises_retryable_exchange_error():
    """부정 테스트 1: 재시도 예산(max_attempts=4)을 넘기면 RetryableExchangeError로
    호출부에 명확히 알린다 -- 무한 재시도로 조용히 걸려있지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"msg": "rate limited"})

    delays, fake_sleep = _fake_sleep_recorder()
    adapter = BinanceAdapter(
        "key", "secret", http_client=_mock_client(handler), sleep_fn=fake_sleep
    )
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTCUSDT")
    assert len(delays) == 3  # 최초 1회 + 재시도 3회 = attempt 1..3에서 대기


async def test_400_client_error_raises_fatal_without_retry():
    """부정 테스트 2: 4xx(잘못된 요청) 응답은 재시도하지 않고 즉시
    FatalExchangeError -- 잘못된 파라미터를 반복 전송하는 사고 방지."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTCUSDT")
    assert calls["n"] == 1


async def test_non_json_response_raises_fatal_exchange_error():
    """부정 테스트 3 + 장애주입: 200이지만 JSON이 아닌 응답(장애) 은
    조용히 실패하지 않고 명시적으로 FatalExchangeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTCUSDT")


async def test_health_check_true_on_ping_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ping"
        return httpx.Response(200, json={})

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    assert await adapter.health_check() is True


async def test_health_check_false_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"msg": "down"})

    delays, fake_sleep = _fake_sleep_recorder()
    adapter = BinanceAdapter(
        "key", "secret", http_client=_mock_client(handler), sleep_fn=fake_sleep
    )
    assert await adapter.health_check() is False


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_get_ticker_round_trip_latency_budget():
    """숫자 성능 단언: MockTransport(네트워크 없음) 기준 100회 호출 평균
    5ms 미만 -- 서명/분류/재시도 오버헤드 자체의 회귀 가드."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_TICKER_JSON)

    adapter = BinanceAdapter("key", "secret", http_client=_mock_client(handler))
    start = time.perf_counter()
    for _ in range(100):
        await adapter.get_ticker("BTCUSDT")
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.005
