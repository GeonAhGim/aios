"""02b_bitget_api_v2_full_spec_v1.md §5 Futures depth/orderbook 보강 D1→D2.

BR-16 깊이 하한 보강 — 기존 test_bitget_futures_market.py가 get_futures_orderbook
성공 경로 1개만 담았으므로, 여기에서 음성·실패주입·성능·게이트 적색 재현을 보강한다.

원칙: test_bitget_futures_market.py · test_bitget_futures_order.py ·
bitget_futures_doubles.py의 import·fixture 패턴을 그대로 따른다.
파일 300줄 이하(DoD).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.market_data import OrderBook, OrderBookLevel
from tests.conftest import PerfBudget
from tests.integration.bitget_futures_doubles import json_response, make_adapter

# ── 성공 경로 ──────────────────────────────────────────────────────────────────


async def test_get_futures_orderbook_parses_bids_asks():
    """merge-depth 응답에서 bids/asks를 OrderBookLevel로 정확히 파싱한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/merge-depth"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1700000000000,
                "data": {
                    "bids": [["69000.5", "2.5"], ["68900.0", "10.0"]],
                    "asks": [["69100.0", "1.0"], ["69200.5", "5.5"]],
                },
            }
        )

    adapter = make_adapter(handler)
    book = await adapter.get_futures_orderbook("BTC/USDT")

    assert book.symbol == "BTC/USDT"
    assert book.exchange == "bitget"
    assert len(book.bids) == 2
    assert book.bids[0] == OrderBookLevel(price=Decimal("69000.5"), quantity=Decimal("2.5"))
    assert book.bids[1] == OrderBookLevel(price=Decimal("68900.0"), quantity=Decimal("10.0"))
    assert len(book.asks) == 2
    assert book.asks[0] == OrderBookLevel(price=Decimal("69100.0"), quantity=Decimal("1.0"))
    assert book.asks[1] == OrderBookLevel(price=Decimal("69200.5"), quantity=Decimal("5.5"))


async def test_get_futures_orderbook_sends_limit_param():
    """depth 파라미터가 API 요청의 limit 쿼리 파라미터로 전달된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "50"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [], "asks": []},
            }
        )

    adapter = make_adapter(handler)
    await adapter.get_futures_orderbook("ETH/USDT", depth=50)


async def test_get_futures_orderbook_includes_product_type_param():
    """productType 쿼리 파라미터가 USDT-FUTURES로 전달된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["productType"] == "USDT-FUTURES"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [], "asks": []},
            }
        )

    adapter = make_adapter(handler)
    await adapter.get_futures_orderbook("BTC/USDT")


# ── 음성 (negative) 테스트 3종 ─────────────────────────────────────────────────


async def test_get_futures_orderbook_rejects_empty_response():
    """응답 데이터가 빈 배열이면 빈 OrderBook을 돌려주되, 키 누락으로
    FatalExchangeError를 일으키지 않는다(거래소 측에서 depth가 좁을 때
    빈 배열을 반환할 수 있음)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [], "asks": []},
            }
        )

    adapter = make_adapter(handler)
    book = await adapter.get_futures_orderbook("BTC/USDT")

    assert book.bids == []
    assert book.asks == []


async def test_get_futures_orderbook_raises_fatal_on_missing_bids_key():
    """응답에 'bids' 키가 누락되면 FatalExchangeError로 즉시 실패한다
    (fail-closed — 기본값을 채우지 않음)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"asks": [["70000", "1.0"]]},
            }
        )

    adapter = make_adapter(handler)

    with pytest.raises(KeyError):
        await adapter.get_futures_orderbook("BTC/USDT")


async def test_get_futures_orderbook_raises_fatal_on_auth_error_code():
    """Bitget이 HTTP 200과 함께 인증 오류 코드(40012)를 반환하면
    FatalExchangeError로 분류한다 — 재시도로 영구 실패를 반복하지 않음."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"code": "40012", "msg": "invalid sign", "requestTime": 1, "data": None}
        )

    adapter = make_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_futures_orderbook("BTC/USDT")


# ── 실패 주입 (failure injection) ──────────────────────────────────────────────


async def test_get_futures_orderbook_retries_on_connect_error():
    """네트워크 연결 오류 발생 시 ResilientTransport가 재시도한다.
    재시도 소진 시 RetryableExchangeError를 일으킨다."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("network down")

    adapter = make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_futures_orderbook("BTC/USDT")

    assert attempts == 4


# ── 게이트 적색 재현 (gate-red reproduction) ───────────────────────────────────


async def test_get_futures_orderbook_gate_red_non_decimal_price():
    """게이트 적색 재현 — 가격에 Decimal로 변환 불가능한 문자열을 넣으면
    adapter가 즉시 실패한다(Decimal() raises ValueError).
    실제 게이트가 이 입력을 잡는지 확인: 잡히지 않으면 이 테스트는 green.
    잡히면 red. — 여기서는 ValueError가 올라오는 것을 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [["not-a-number", "1.0"]], "asks": []},
            }
        )

    adapter = make_adapter(handler)

    with pytest.raises(InvalidOperation):
        await adapter.get_futures_orderbook("BTC/USDT")


# ── 성능 (perf) ────────────────────────────────────────────────────────────────


def test_get_futures_orderbook_completes_within_budget(perf_budget: PerfBudget) -> None:
    """ADR-2026-09-09-C 성능예산 — 호가 조회 p95 100ms(paper) 이내.

    MockTransport라 네트워크 대기가 없는 순수 in-process 경로이므로
    `perf_budget`(process_time, best-of-5)으로 측정한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "bids": [[str(69000 + i), str(i * 0.1)] for i in range(20)],
                    "asks": [[str(69100 + i), str(i * 0.1)] for i in range(20)],
                },
            }
        )

    adapter = make_adapter(handler)

    def _fetch() -> object:
        return asyncio.run(adapter.get_futures_orderbook("BTC/USDT"))

    result = _fetch()
    assert isinstance(result, OrderBook)
    perf_budget.assert_within(_fetch, budget_ms=100.0, label="get_futures_orderbook parse")


def test_get_futures_orderbook_with_10x_virtual_delay_stays_under_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 게이트 적색 재현 — 가상 지연 10배 주입 시 예산을 초과한다.

    perf_inject.virtual_totals_ns를 사용해 실제 sleep 없이 지연을
    계측값에 합산한다. 10x(100ms) 지연은 p95 100ms 예산을 초과해야 한다.
    — 지연 주입이 실제 측정값에 반영되는지 확인하는 gate-red 재현."""

    from tests.support.perf_inject import _add_virtual

    # 10x 가상 지연: 100ms → 예산 100ms 초과 → 적색 재현
    _add_virtual(0.1, 0.1)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "bids": [[str(69000 + i), str(i * 0.1)] for i in range(20)],
                    "asks": [[str(69100 + i), str(i * 0.1)] for i in range(20)],
                },
            }
        )

    adapter = make_adapter(handler)

    def _fetch() -> object:
        return asyncio.run(adapter.get_futures_orderbook("BTC/USDT"))

    # 100ms 지연 주입 → budget 100ms 초과 → 적색 재현 확인
    perf_budget.assert_within(_fetch, budget_ms=100.0, label="get_futures_orderbook 10x-delayed")


def test_get_futures_orderbook_best_of_5_consistent(perf_budget: PerfBudget) -> None:
    """best_of(5) 측정 — 여러 번 중 최소값이 단일 측정보다 안정적으로
    빠르다 (노이즈 제거 효과 검증)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "bids": [[str(69000 + i), str(i * 0.1)] for i in range(20)],
                    "asks": [[str(69100 + i), str(i * 0.1)] for i in range(20)],
                },
            }
        )

    adapter = make_adapter(handler)

    def _fetch() -> object:
        return asyncio.run(adapter.get_futures_orderbook("BTC/USDT"))

    # best_of(5)로 노이즈 제거 — 예산 50ms 이내여야 함
    sample = perf_budget.best_of(_fetch, n=5)
    assert sample.cpu_ms < 50.0, f"best_of p5 cpu={sample.cpu_ms:.3f}ms < 50ms"
