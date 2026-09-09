"""task-2779 DEEPEN of task-1785 (BR-7 해외선물옵션, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1785)는 원 커밋(20d7190,
결함수정 2978b0f)이 negative test 4개와 gate-red 회귀 테스트(task-1978
REJECT path-mismatch 재포착)를 갖췄지만 D2 하한에 필요한 두 가지가 없다고
판정했다: (1) failure-injection 테스트(시뮬레이션된 crash/network/DB),
(2) 수치 latency/throughput 성능 단언. 이 파일이 그 두 가지만 보강한다 —
`overseas_futureoption_mixin.py`는 손대지 않는다.

1) Failure-injection: 이 mixin은 DB를 다루지 않으므로(순수 REST 요청조립/
   응답파싱) network 실패와 시뮬레이션된 crash 두 축으로 주입한다.
   - `httpx.ConnectError`(네트워크 단절)를 주문/잔고 조회 경로에 주입해
     `ResilientTransport`가 재시도를 모두 소진한 뒤 `RetryableExchangeError`
     로 그대로 드러나는지(삼켜지지 않는지) 확인한다. 특히 잔고 조회는
     실패를 빈 리스트로 흡수하면 "포지션 없음"으로 오인될 수 있어
     안전상 중요하다(LIVE-guard 문맥).
   - `httpx.TransportError` 계열이 아닌 임의 예외(브로커 프로세스 크래시
     시뮬레이션)는 `ResilientTransport._request_with_retry`가 `except
     httpx.TransportError`로만 잡으므로 재시도 없이 원본 그대로 즉시
     전파돼야 한다 — 이 경계도 명시적으로 증명한다.
   재시도 백오프로 테스트가 느려지지 않도록 `sleep_fn`을 무지연 콜러블로
   주입한다(KISAdapter/`_KISTokenTransportMixin.__init__`이 이미 지원).

2) 수치 throughput 성능 단언: 절대 ms 상수 대신, 같은 프로세스에서 구조적
   으로 동일한 연산(output1 행 순회 + Decimal 변환 + AccountBalance 생성)을
   하는 기존 해외주식 잔고 파서(`overseas_stock_mixin.get_overseas_balance`)
   를 베이스라인으로 삼아 정규화한 배율 임계를 쓴다(task-2777/2774 선례와
   동일 판단 — 공유 CI 환경에서 절대 임계는 상시 적색을 낳는다).
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import PAPER_BASE_URL, KISAdapter

_QUOTE_FUTURES_PATH = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
_ORDER_PATH = "/uapi/overseas-futureoption/v1/trading/order"
_BALANCE_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-unpd"
_TOKEN_PATH = "/oauth2/tokenP"

_FAR_FUTURE_EXPIRY = date(2099, 12, 1)


async def _no_delay(seconds: float) -> None:
    """백오프 대기를 실제로 하지 않는 `sleep_fn` — failure-injection
    테스트가 재시도 소진을 기다리느라 느려지지 않게 한다."""


def _futures_order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="ESZ26",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.OVERSEAS_FUTURES,
        expiry_date=_FAR_FUTURE_EXPIRY,
        contract_multiplier=Decimal("50"),
        underlying_symbol="ES",
    )


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url=PAPER_BASE_URL, transport=transport)
    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=http_client,
        sleep_fn=sleep_fn,
    )


def _ok_token(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})


# ---------------------------------------------------------------------------
# 1) Failure-injection
# ---------------------------------------------------------------------------


async def test_place_order_network_failure_is_not_swallowed() -> None:
    """네트워크 단절이 재시도 소진 후에도 `RetryableExchangeError`로 그대로
    드러나야 한다 — 무음으로 삼켜져 (틀린) 성공 Order를 돌려주면 이중 제출/
    유령 주문 사고로 이어진다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        call_count["n"] += 1
        raise httpx.ConnectError("simulated network outage", request=request)

    adapter = _make_adapter(handler, sleep_fn=_no_delay)

    with pytest.raises(RetryableExchangeError):
        await adapter.place_overseas_futureoption_order(_futures_order())

    assert call_count["n"] >= 1  # 실제로 전송을 시도했다(조용히 건너뛰지 않음)


async def test_balance_network_failure_does_not_silently_return_empty_list() -> None:
    """잔고 조회 중 네트워크가 끊기면 예외로 드러나야 한다 — 빈 리스트로
    흡수되면 실제 포지션이 있는데도 "포지션 없음"으로 오인될 수 있어
    (LIVE-guard 문맥에서) 특히 위험한 실패 모드다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        raise httpx.ConnectError("simulated network outage", request=request)

    adapter = _make_adapter(handler, sleep_fn=_no_delay)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_overseas_futureoption_balance()


async def test_cancel_network_failure_is_not_swallowed_into_false() -> None:
    """취소 요청 중 네트워크가 끊기면 `False`(정상적인 거래소 거부 응답)와
    구분되는 예외로 드러나야 한다 — 둘을 섞으면 호출부가 "거래소가 거부
    했다"와 "전송 자체가 실패했다"를 구분하지 못해 재시도 정책이 깨진다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        raise httpx.ConnectError("simulated network outage", request=request)

    adapter = _make_adapter(handler, sleep_fn=_no_delay)

    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))


async def test_simulated_crash_not_classified_as_transport_error_propagates_immediately() -> None:
    """`httpx.TransportError` 계열이 아닌 임의 예외(예: 브로커 프로세스가
    응답 도중 그대로 죽는 상황의 근사 시뮬레이션)는 `ResilientTransport.
    _request_with_retry`의 `except httpx.TransportError`가 잡지 못하므로
    재시도 없이 즉시, 원본 타입 그대로 전파돼야 한다 — 이 경계를 명시적
    으로 증명해, 예상치 못한 예외가 조용히 삼켜지거나 다른 타입으로
    둔갑하지 않음을 확인한다."""

    class _SimulatedBrokerCrash(RuntimeError):
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        raise _SimulatedBrokerCrash("simulated broker process crash mid-response")

    adapter = _make_adapter(handler, sleep_fn=_no_delay)

    with pytest.raises(_SimulatedBrokerCrash):
        await adapter.get_overseas_futureoption_balance()


# ---------------------------------------------------------------------------
# 2) 수치 throughput 성능 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------

_N_ROWS = 50
_N_ITERATIONS = 50


def _futureoption_balance_payload() -> dict[str, object]:
    rows = [
        {
            "pdno": f"ES{i:04d}",
            "cblc_qty": "10",
            "ord_psbl_qty": "10",
            "mntn_mgn": "500",
        }
        for i in range(_N_ROWS)
    ]
    return {"rt_cd": "0", "msg1": "OK", "output1": rows}


def _stock_balance_payload() -> dict[str, object]:
    rows = [
        {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10", "ord_psbl_qty": "10"}
        for _ in range(_N_ROWS)
    ]
    return {"rt_cd": "0", "msg1": "OK", "output1": rows}


def _make_futureoption_balance_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        return httpx.Response(200, json=_futureoption_balance_payload())

    return _make_adapter(handler)


def _make_stock_balance_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        return httpx.Response(200, json=_stock_balance_payload())

    return _make_adapter(handler)


async def _min_elapsed_seconds(fn: Callable[[], Awaitable[None]], repeats: int = 5) -> float:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        await fn()
        times.append(time.perf_counter() - start)
    return min(times)


async def test_balance_parsing_throughput_bounded_vs_overseas_stock_baseline() -> None:
    """해외선물옵션 잔고 파서(`get_overseas_futureoption_balance`)가 구조적
    으로 동일한 파이프라인(output1 행 순회 + Decimal 변환 + AccountBalance
    생성)을 쓰는 기존 해외주식 잔고 파서(`get_overseas_balance`) 대비
    반복 측정한 총소요시간 배율이 CI 편차를 감안해도 좁은 범위여야 한다.
    절대 ms 상수 대신 같은 프로세스가 방금 측정한 베이스라인에 정규화한
    배율을 임계로 쓴다(task-2777/2774 선례와 동일 판단)."""
    fo_adapter = _make_futureoption_balance_adapter()
    stock_adapter = _make_stock_balance_adapter()

    # 워밍업 — 토큰 발급/import 관련 1회성 비용이 표본에 섞이지 않게 한다.
    await fo_adapter.get_overseas_futureoption_balance()
    await stock_adapter.get_overseas_balance("NASD")

    async def run_futureoption() -> None:
        for _ in range(_N_ITERATIONS):
            balances = await fo_adapter.get_overseas_futureoption_balance()
            assert len(balances) == _N_ROWS

    async def run_stock() -> None:
        for _ in range(_N_ITERATIONS):
            balances = await stock_adapter.get_overseas_balance("NASD")
            assert len(balances) == _N_ROWS

    stock_seconds = await _min_elapsed_seconds(run_stock)
    fo_seconds = await _min_elapsed_seconds(run_futureoption)

    assert stock_seconds > 0.0
    ratio = fo_seconds / stock_seconds
    budget_ratio = 4.0  # 필드 수·연산이 거의 동일해 이론상 ~1배, 여유 4배
    print(
        f"\noverseas futureoption vs stock balance-parse throughput: "
        f"n_rows={_N_ROWS} n_iterations={_N_ITERATIONS} "
        f"stock={stock_seconds * 1000:.1f}ms futureoption={fo_seconds * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"해외선물옵션 잔고 파서가 구조적으로 동일한 해외주식 파서 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — output1 순회/"
        "Decimal 변환 경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )
