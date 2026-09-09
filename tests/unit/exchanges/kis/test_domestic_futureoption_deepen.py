"""task-2778 DEEPEN of task-1784 (BR-6, docs/audit/DEPTH_L4_BR.md #1784,
ADR-2026-09-06-I D2).

DEPTH 감사는 원 커밋(863bcc6, task-2008로 결함 수정됨)이 negative test
4개와 gate-red 회귀 테스트(task-1977/2005 REJECT 재발 방지)까지 갖췄지만
D2 하한에 필요한 두 가지가 없다고 판정했다: (1) 시뮬레이션된 crash/network/
DB failure-injection 테스트, (2) 수치 지연/처리량 성능 단언. 이 파일이 그
두 가지만 보강한다 — `domestic_futureoption_mixin.py`는 손대지 않는다.

DB 실패주입은 이 mixin 스콥에 해당하지 않는다 — `KISDomesticFutureoptionMixin`은
REST 왕복만 하고 자체적으로 DB에 쓰지 않으므로(주문 영속화는 상위 OMS
계층 책임), 여기서는 network(연결 실패) + crash(미분류 예외) 두 축만
주입한다.

1) failure-injection — `place_futureoption_order`에 `httpx.ConnectError`를
   반복 주입해 `ResilientTransport`가 재시도 정책을 소진한 뒤
   `RetryableExchangeError`로 fail-closed 전파됨을 증명한다(무음 성공
   금지 — 응답 없이 `Order`가 SUBMITTED로 둔갑하면 안 된다). 재시도
   백오프로 테스트가 느려지지 않도록 `sleep_fn`을 무지연 함수로 주입한다
   (`oauth_client.py::_KISTokenTransportMixin.__init__`가 지원).
2) failure-injection — `cancel_futureoption_order`에 미분류 예외(단순
   `RuntimeError` — 프로세스 크래시에 준하는 상황을 흉내)를 주입해
   `ResilientTransport`가 이를 삼키지 않고 그대로 전파함을 증명한다
   (`httpx.TransportError`가 아닌 예외는 재시도 대상도 아니고 조용히
   `False`로 뭉개져도 안 된다).
3) 수치 처리량 단언 — `get_futureoption_balance`가 다건(2000행) 응답을
   파싱하는 실측 소요시간을, 같은 프로세스에서 측정한 동일 N 크기의
   trivial Decimal 생성 루프에 정규화한 배율로 단언한다(절대 ms 상수 대신
   — task-2773/test_kis_tr_coverage.py, task-2777/
   test_kis_ws_overseas_deepen.py와 동일 판단, 공유 CI 환경에서 절대
   임계는 상시 적색을 낳는다).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter

_QUOTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_CANCEL_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance"


async def _no_delay_sleep(_seconds: float) -> None:
    """재시도 백오프를 없애 실패주입 테스트가 실제 대기 없이 즉시 끝나게 한다."""
    await asyncio.sleep(0)


def _make_adapter(handler: Callable[[httpx.Request], httpx.Response]) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=http_client,
        sleep_fn=_no_delay_sleep,
    )


def _futures_order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="101W09",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_FUTURES,
        expiry_date=None,
        contract_multiplier=Decimal("250000"),
        underlying_symbol="KOSPI200",
    )


# ---------------------------------------------------------------------------
# 1) failure-injection — 네트워크 연결 실패(주문 제출)
# ---------------------------------------------------------------------------


async def test_place_order_network_failure_propagates_fail_closed_after_retries() -> None:
    """모의투자 주문 제출 중 매 시도마다 연결이 끊기면(`httpx.ConnectError`),
    `ResilientTransport`가 재시도 정책(`RetryPolicy.max_attempts=4`)을 다
    소진한 뒤 `RetryableExchangeError`로 fail-closed 전파해야 한다 — 응답을
    못 받았는데 주문이 SUBMITTED로 조용히 성공 처리되면 실거래에서 이중
    주문/유령 주문 사고로 이어진다."""
    attempt_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        nonlocal attempt_count
        attempt_count += 1
        raise httpx.ConnectError("simulated network failure", request=request)

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.place_futureoption_order(_futures_order())

    # 재시도 정책이 실제로 여러 번 시도한 뒤에야 포기했음을 확인한다
    # (첫 실패에서 바로 포기하는 것도, 무한 재시도로 새는 것도 결함).
    assert attempt_count > 1
    assert attempt_count == 4  # http_policy.RetryPolicy 기본 max_attempts


# ---------------------------------------------------------------------------
# 2) failure-injection — 미분류 예외(크래시 시뮬레이션, 주문 취소)
# ---------------------------------------------------------------------------


async def test_cancel_order_simulated_crash_propagates_unmasked() -> None:
    """취소 요청 도중 프로세스 레벨 이상(예: 워커 크래시)을 흉내 낸 미분류
    예외(`RuntimeError`, `httpx.TransportError`가 아니므로 재시도 대상도
    아님)가 발생하면, `ResilientTransport`/`cancel_futureoption_order`
    어느 층에서도 이를 삼켜 `False`(취소 실패했지만 "정상 응답")로
    뭉개서는 안 된다 — 호출부가 실제로는 미확정 상태를 확정 실패로 오인해
    잘못된 후속 조치(예: 재주문)를 할 위험이 있다."""

    class _SimulatedCrash(RuntimeError):
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        if request.url.path == _CANCEL_PATH:
            raise _SimulatedCrash("simulated worker crash mid-request")
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": {}})

    adapter = _make_adapter(handler)

    with pytest.raises(_SimulatedCrash):
        await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))


# ---------------------------------------------------------------------------
# 3) 수치 처리량 단언 — 잔고 파싱(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _synthetic_balance_rows(n: int) -> list[dict[str, str]]:
    return [
        {
            "pdno": f"10{i:05d}",
            "cblc_qty": "1",
            "ord_psbl_qty": "1",
            "mntn_mgn": "125000",
        }
        for i in range(n)
    ]


async def test_balance_parsing_throughput_within_normalized_budget() -> None:
    """`get_futureoption_balance`가 2000행 응답을 파싱하는 실측 소요시간을
    동일 N 크기의 trivial Decimal 생성 루프(같은 프로세스, 같은 측정
    시점) 대비 정규화한 배율로 단언한다 — 절대 ms 상수는 공유 CI에서
    상시 적색을 낳으므로(task-2773/2777 선례) 쓰지 않는다."""
    n = 2000
    repeats = 5

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        payload = {"rt_cd": "0", "msg1": "OK", "output": {}, "output1": _synthetic_balance_rows(n)}
        return httpx.Response(200, json=payload)

    adapter = _make_adapter(handler)

    # 워밍업 — import/JIT 관련 1회성 비용이 표본에 섞이지 않게 한다.
    warmup = await adapter.get_futureoption_balance()
    assert len(warmup) == n

    balance_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        balances = await adapter.get_futureoption_balance()
        balance_times.append(time.perf_counter() - start)
    assert len(balances) == n
    balance_seconds = min(balance_times)

    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        baseline = [Decimal(f"10{i:05d}") for i in range(n)]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = balance_seconds / baseline_seconds
    # httpx MockTransport JSON 직렬화/역직렬화 오버헤드 포함, 측정치(~13배) 대비 ~4.5배
    # 여유(task-2773/test_kis_tr_coverage.py와 동일 여유 폭 판단).
    budget_ratio = 60.0
    print(
        f"\nbalance parse throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms balance={balance_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_futureoption_balance가 trivial Decimal 생성 루프 대비 {ratio:.1f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — 응답 파싱 경로에 의도치 않은 무거운 "
        "연산이 섞였을 가능성."
    )
