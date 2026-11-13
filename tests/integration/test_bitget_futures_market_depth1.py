"""Bitget Futures 시장조회 D2 보강: 음성 3종, 실패 주입, 성능 예산의 적색 재현.

기존 test_bitget_futures_market.py의 make_adapter/json_response 방식을 그대로 쓰고
앱 코드는 변경하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.bitget.adapter import BitgetAdapter
from tests.integration.bitget_futures_doubles import json_response, make_adapter
from tests.support.perf_budget import PerfBudget, PerfSample
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_BOOK = {"bids": [["80900", "1.5"]], "asks": [["80950", "2.0"]]}


def _ok(data: object) -> httpx.Response:
    return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": data})


async def test_negative_orderbook_missing_asks_fails_closed():
    """asks 키가 없는 응답을 빈 호가창으로 얼버무리지 않고 예외로 거부한다."""
    adapter = make_adapter(lambda request: _ok({"bids": [["80900", "1.5"]]}))
    with pytest.raises(KeyError):
        await adapter.get_futures_orderbook("BTC/USDT")


async def test_negative_orderbook_non_numeric_price_rejected():
    """숫자가 아닌 가격 문자열은 Decimal 변환에서 거부되고 float로 우회되지 않는다."""
    adapter = make_adapter(lambda request: _ok({"bids": [["abc", "1"]], "asks": []}))
    with pytest.raises(ArithmeticError):
        await adapter.get_futures_orderbook("BTC/USDT")


async def test_negative_ticker_empty_data_fails_closed():
    """ticker data가 빈 리스트면 가짜 가격 없이 예외로 끝난다."""
    adapter = make_adapter(lambda request: _ok([]))
    with pytest.raises((IndexError, KeyError)):
        await adapter.get_futures_ticker("BTC/USDT")


async def test_negative_candles_unsupported_timeframe_sends_no_request():
    """지원하지 않는 timeframe은 요청 전에 ValueError로 거부된다."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _ok([])

    adapter = make_adapter(handler)
    with pytest.raises(ValueError):
        await adapter.get_futures_candles("BTC/USDT", "7m")
    assert calls == []


async def test_failure_injection_http_503_error_not_swallowed():
    """HTTP 5xx 서버 오류가 계속되면 성공 응답으로 위장되지 않고 예외로 전파된다."""

    async def sleep_noop(_delay: float) -> None:
        return None

    adapter = make_adapter(
        lambda request: json_response({"code": "50000", "msg": "boom"}, status_code=503),
        sleep_fn=sleep_noop,
    )
    with pytest.raises((RetryableExchangeError, FatalExchangeError)):
        await adapter.get_futures_orderbook("BTC/USDT")


def _assert_orderbook_budget(sample: PerfSample):
    assert sample.wall_ms < 1000.0, f"BR-16c orderbook budget: {sample.wall_ms:.3f}ms"


async def _fetch_book(adapter: BitgetAdapter):
    book = await adapter.get_futures_orderbook("BTC/USDT")
    assert book.bids[0].price == Decimal("80900")
    assert book.asks[0].quantity == Decimal("2.0")
    return book


@pytest.mark.perf
async def test_perf_orderbook_budget_and_gate_red_10x_delay(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
):
    """merge-depth 조회 1회(MockTransport 왕복 + Decimal 파싱) 예산.

    예산 근거: 로컬 best-of-5 실측 wall 0.387ms (2026-11-14). 1000ms는 그 30배 이상의 여유다.
    예산의 10배(10초)를 요청 경로에 가상 주입해 같은 단언이 적색인지 확인한다.
    """
    reset_virtual_totals()
    adapter = make_adapter(lambda request: _ok(_BOOK))

    healthy = await perf_budget.best_of_async(lambda: _fetch_book(adapter), n=5, warmup=1)
    print(f"BR-16c orderbook baseline cpu={healthy.cpu_ms:.3f}ms wall={healthy.wall_ms:.3f}ms")
    _assert_orderbook_budget(healthy)

    monkeypatch.setattr(adapter, "_request", virtual_delayed_async(adapter._request, 10.0))
    degraded = await perf_budget.best_of_async(lambda: _fetch_book(adapter), n=5)
    assert degraded.wall_ms >= 10000.0
    with pytest.raises(AssertionError, match="BR-16c orderbook budget"):
        _assert_orderbook_budget(degraded)
