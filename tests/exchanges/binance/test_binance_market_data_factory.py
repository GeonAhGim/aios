"""task-7600 -- market data mixin, venue_profile wiring, BR-9 registration.

task-24103 -- DEEPEN-DEPTH BR-22b 깊이 하한 보강 D1→D2: failure_injection,
perf, gate_red 추가.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.binance import factory as bfactory
from src.exchanges.binance.factory import BinanceAdapter
from src.exchanges.binance.market_data_mixin import BinanceMarketDataMixin
from src.exchanges.binance.venue_profile import BINANCE_SPOT_PROFILE
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.factory import build_adapter, register_exchange_adapter_factory
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


def _adapter(payload):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
        base_url="https://x.test",
    )
    return BinanceAdapter("k", "s", http_client=http)


@pytest.mark.parametrize("name", ["get_ticker", "get_orderbook", "get_ohlcv"])
def test_signature_matches_abc(name):
    assert inspect.signature(getattr(BinanceMarketDataMixin, name)).parameters.keys() == (
        inspect.signature(getattr(ExchangeAdapter, name)).parameters.keys()
    )


async def test_get_ticker():
    t = await _adapter(
        {
            "lastPrice": "100.5",
            "bidPrice": "100",
            "askPrice": "101",
            "volume": "7",
            "closeTime": 1700000000000,
        }
    ).get_ticker("BTC/USDT")
    assert t.price == Decimal("100.5") and t.exchange == "binance"


async def test_get_orderbook():
    ob = await _adapter({"bids": [["1", "2"]], "asks": [["3", "4"]]}).get_orderbook("BTC/USDT", 5)
    assert ob.bids[0].price == Decimal("1") and ob.asks[0].quantity == Decimal("4")


async def test_get_ohlcv():
    row = [1700000000000, "1", "2", "0.5", "1.5", "10", 1700000059999]
    c = await _adapter([row]).get_ohlcv("BTC/USDT", "1m", 1)
    assert c[0].high == Decimal("2") and c[0].close_time > c[0].open_time


async def test_malformed_ticker_is_fatal():
    with pytest.raises(FatalExchangeError):
        await _adapter({"lastPrice": "x"}).get_ticker("BTC/USDT")


async def test_get_ticker_bidPrice_missing_raises():
    """bidPrice 누락 → 예외 (silent fallback 제거)."""
    with pytest.raises(FatalExchangeError, match="bidPrice"):
        await _adapter(
            {"lastPrice": "100", "askPrice": "101", "volume": "5", "closeTime": 1700000000000}
        ).get_ticker("BTC/USDT")


async def test_get_ticker_askPrice_missing_raises():
    """askPrice 누락 → 예외 (silent fallback 제거)."""
    with pytest.raises(FatalExchangeError, match="askPrice"):
        await _adapter(
            {"lastPrice": "100", "bidPrice": "99", "volume": "5", "closeTime": 1700000000000}
        ).get_ticker("BTC/USDT")


async def test_get_ticker_bidPrice_empty_string_raises():
    """bidPrice가 빈 문자열 → 예외."""
    with pytest.raises(FatalExchangeError, match="bidPrice"):
        await _adapter(
            {
                "lastPrice": "100",
                "bidPrice": "",
                "askPrice": "101",
                "volume": "5",
                "closeTime": 1700000000000,
            }
        ).get_ticker("BTC/USDT")


async def test_get_ticker_bidPrice_zero_raises():
    """bidPrice가 '0' → 예외."""
    with pytest.raises(FatalExchangeError, match="bidPrice"):
        await _adapter(
            {
                "lastPrice": "100",
                "bidPrice": "0",
                "askPrice": "101",
                "volume": "5",
                "closeTime": 1700000000000,
            }
        ).get_ticker("BTC/USDT")


async def test_malformed_klines_is_fatal():
    with pytest.raises(FatalExchangeError):
        await _adapter([[1]]).get_ohlcv("BTC/USDT", "1m")


def test_venue_profile_wired():
    a = BinanceAdapter("k", "s")
    assert a.venue_profile() is BINANCE_SPOT_PROFILE
    assert BINANCE_SPOT_PROFILE.verified == "DOC_ONLY"


def test_registered_via_br9_extension_point():
    register_exchange_adapter_factory(bfactory.EXCHANGE_NAME, bfactory._binance_adapter_factory)
    assert isinstance(build_adapter("binance", "k", "s", {}, demo_mode=True), BinanceAdapter)


async def test_ticker_stream_unsupported_fail_closed():
    with pytest.raises(Exception, match="subscribe_ticker_stream"):
        await BinanceAdapter("k", "s").subscribe_ticker_stream("BTC/USDT", lambda _t: None)


async def test_extension_query_unsupported_fail_closed():
    with pytest.raises(Exception, match="find_order_by_client_id"):
        await BinanceAdapter("k", "s").find_order_by_client_id("cid")


# ---- 추가 음성(negative) 테스트 3종 (task-24103 D2 보강) ----


async def test_get_orderbook_empty_bids_asks_succeeds():
    """bids/asks가 빈 배열이어도 예외 없이 빈 OrderBook을 돌려야 한다."""
    ob = await _adapter({"bids": [], "asks": []}).get_orderbook("BTC/USDT", 5)
    assert ob.bids == [] and ob.asks == []


async def test_get_ohlcv_empty_array_succeeds():
    """klines가 빈 배열이면 빈 리스트를 돌려야 한다."""
    c = await _adapter([]).get_ohlcv("BTC/USDT", "1m")
    assert c == []


async def test_get_ticker_volume_missing_defaults_to_zero():
    """volume 필드가 없으면 '0'으로 처리해야 한다 (source 코드 default 참조)."""
    t = await _adapter(
        {
            "lastPrice": "100.5",
            "bidPrice": "100",
            "askPrice": "101",
            "closeTime": 1700000000000,
        }
    ).get_ticker("BTC/USDT")
    assert t.volume_24h == Decimal("0")


# ---- 실패 주입 + 게이트 적색 재현 + 성능 단언 (task-24103 D2 보강) ----


@pytest.mark.perf
async def test_get_ticker_performance_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24103): 정상적인 get_ticker 호출이 CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 get_ticker(모의 transport)는 ~0.05ms CPU.
    30배 여유인 1.5ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    async def _call() -> None:
        await _adapter(
            {
                "lastPrice": "100.5",
                "bidPrice": "100",
                "askPrice": "101",
                "volume": "7",
                "closeTime": 1700000000000,
            }
        ).get_ticker("BTC/USDT")

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.05ms × 30 = 1.5ms 예산
    assert sample.cpu_ms < 1.5, (
        f"get_ticker가 예산을 초과: {perf_budget.describe(sample, budget_ms=1.5)}"
    )


@pytest.mark.perf
async def test_gate_red_virtual_delay_exceeds_ticker_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현 (D2, task-24103): virtual_delayed_sync로 가상 지연
    (예산 10배)을 주입하고, 같은 CPU 단언이 초과함을 확인한다.

    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 측정치에 지연을 더할 뿐 실제 테스트 시간이 늘어나지
    않는다. green 테스트와 동일한 `cpu_ms < budget_ms` 단언을 쓰고,
    지연 주입 시 `cpu_ms >= budget_ms`로 적색을 재현한다.
    """
    reset_virtual_totals()

    budget_ms = 1.5
    # 10배 가상 지연 (budget_ms * 10 / 1000 → 초 단위)
    injected_delay_s = budget_ms * 10 / 1000.0

    raw_payload = {
        "lastPrice": "100.5",
        "bidPrice": "100",
        "askPrice": "101",
        "volume": "7",
        "closeTime": 1700000000000,
    }
    adapter = _adapter(raw_payload)

    # _request에 가상 지연 주입 — virtual_delayed_sync가 cpu+wall 양쪽에 기록
    monkeypatch.setattr(
        adapter,
        "_request",
        virtual_delayed_sync(adapter._request, injected_delay_s),
    )

    async def _delayed_call() -> None:
        await adapter.get_ticker("BTC/USDT")

    red = await perf_budget.best_of_async(_delayed_call, n=3, warmup=0)
    # virtual_delayed_sync는 cpu ledger에도 기록하므로 cpu_ms로 단언 —
    # green 테스트와 동일한 단언이 적색으로 전환됨을 확인한다.
    assert red.cpu_ms >= budget_ms, (
        f"가상 지연 주입에도 CPU 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
    )


@pytest.mark.perf
async def test_get_orderbook_performance_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24103): 정상적인 get_orderbook 호출이 CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 get_orderbook(모의 transport)는 ~0.05ms CPU.
    30배 여유인 1.5ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    async def _call() -> None:
        await _adapter({"bids": [["1", "2"]], "asks": [["3", "4"]]}).get_orderbook("BTC/USDT", 5)

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.05ms × 30 = 1.5ms 예산
    assert sample.cpu_ms < 1.5, (
        f"get_orderbook가 예산을 초과: {perf_budget.describe(sample, budget_ms=1.5)}"
    )


@pytest.mark.perf
async def test_get_ohlcv_performance_under_normal_load(perf_budget: PerfBudget) -> None:
    """성능 단언 (D2, task-24103): 정상적인 get_ohlcv 호출이 CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 get_ohlcv(모의 transport)는 ~0.1ms CPU.
    30배 여유인 3.0ms를 예산으로 설정한다.
    """
    reset_virtual_totals()

    row = [1700000000000, "1", "2", "0.5", "1.5", "10", 1700000059999]

    async def _call() -> None:
        await _adapter([row]).get_ohlcv("BTC/USDT", "1m", 1)

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.1ms × 30 = 3.0ms 예산
    assert sample.cpu_ms < 3.0, (
        f"get_ohlcv가 예산을 초과: {perf_budget.describe(sample, budget_ms=3.0)}"
    )
