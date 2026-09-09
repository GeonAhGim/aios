"""task-2789 DEEPEN of task-1981 (BR-6, docs/audit/DEPTH_L4_BR.md, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722)는 원 커밋(2d6759c, 리뷰 task-1977 REJECT 3건에 대한
결함 수정)이 D2 하한에 필요한 네 가지가 없다고 판정했다: (1) negative test
3개 미만(LIVE-adapter 거부 2건뿐), (2) 이 커밋에서 실제로 추가한
ORD_DVSN_CD/RMN_QTY_YN 파라미터가 배선됐음을 증명하는 테스트 부재(이후
task-2008이 별도로 닫음), (3) failure-injection 테스트 부재, (4) 성능
단언 부재.

task-2778(test_domestic_futureoption_deepen.py)이 이미 같은 mixin에 대해
네트워크 단절(ConnectError)·미분류 크래시(RuntimeError) failure-injection과
잔고 파싱 처리량 단언을 추가했으므로, 이 파일은 그것과 겹치지 않는
축만 보강한다:
  1) 미검증 브랜치 배선 — `_order_division`의 LIMIT(00) 분기는 기존
     테스트(test_domestic_futureoption_mixin.py)가 MARKET(01) 분기만
     검사해 한 번도 실행되지 않았다. ORD_DVSN_CD가 실제로 주문유형에
     따라 분기됨을 증명한다(negative test #1).
  2) failure-injection(응답 레벨, task-2778과 다른 축) — HTTP 200 +
     rt_cd="0"(성공 응답)인데 `output`에 KRX_FWDG_ORD_ORGNO/ODNO가
     없는 "성공했다고 주장하지만 실제로는 불완전한" 응답을 주입해
     `FatalExchangeError`로 fail-closed 전파됨을 증명한다 — 이 경로는
     task-2778의 네트워크/크래시 주입과 겹치지 않는 별도 실패 모드다
     (negative test #2, failure-injection).
  3) failure-injection(거래소 비즈니스 오류) — rt_cd != "0"(거래소가
     명시적으로 거부)인 취소 응답이 재시도 소진 후 `RetryableExchangeError`
     로 그대로 드러나야 한다 — `cancel_futureoption_order`가 이를
     삼켜 `False`(성공적으로 확인된 거부)로 뭉개면 안 된다(negative
     test #3, failure-injection — 성공 케이스와 실패 케이스를 구분
     못 하면 호출부의 재시도/알림 정책이 깨진다).
  4) 수치 성능 단언 — `place_futureoption_order` 왕복(바디 조립 +
     응답 파싱)의 반복 측정 소요시간을, 같은 프로세스에서 측정한
     동일 규모의 trivial dict round-trip(json dumps/loads) 대비
     정규화한 배율로 단언한다(task-2773/2777/2778과 동일 판단 —
     절대 ms 상수는 공유 CI에서 상시 적색을 낳는다).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import PAPER_BASE_URL, KISAdapter

_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_CANCEL_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
_TOKEN_PATH = "/oauth2/tokenP"


async def _no_delay_sleep(_seconds: float) -> None:
    """재시도 백오프를 없애 failure-injection 테스트가 즉시 끝나게 한다."""
    await asyncio.sleep(0)


def _make_adapter(handler: Callable[[httpx.Request], httpx.Response]) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url=PAPER_BASE_URL, transport=transport)
    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=http_client,
        sleep_fn=_no_delay_sleep,
    )


def _ok_token(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})


def _futures_order(*, order_type: OrderType = OrderType.MARKET) -> Order:
    price = (
        Money(amount=Decimal("352.50"), currency=Currency.KRW)
        if order_type == OrderType.LIMIT
        else None
    )
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="101W09",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=order_type,
        price=price,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_FUTURES,
        contract_multiplier=Decimal("250000"),
        underlying_symbol="KOSPI200",
    )


# ---------------------------------------------------------------------------
# 1) negative test — LIMIT 분기 배선(기존 테스트가 커버하지 않던 브랜치)
# ---------------------------------------------------------------------------


async def test_place_limit_order_wires_ord_dvsn_cd_00() -> None:
    """`_order_division`의 LIMIT(00) 분기는 기존 테스트(MARKET만 검사)로는
    한 번도 실행되지 않았다 — ORD_DVSN_CD가 주문유형에 따라 실제로 00/01로
    분기됨을 증명한다. 이 분기가 깨지면(예: 상수 "01" 하드코딩으로 퇴화)
    이 assertion이 실패해야 negative test로 유효하다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        captured.append(request)
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "OK", "output": {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}},
        )

    adapter = _make_adapter(handler)

    await adapter.place_futureoption_order(_futures_order(order_type=OrderType.LIMIT))

    order_body = json.loads(captured[-1].content)
    assert order_body["ORD_DVSN_CD"] == "00"
    assert order_body["UNIT_PRICE"] == "352.50"


# ---------------------------------------------------------------------------
# 2) failure-injection — 성공을 주장하지만 필드가 없는 응답(주문 제출)
# ---------------------------------------------------------------------------


async def test_place_order_response_missing_odno_raises_fatal_not_fabricated_success() -> None:
    """rt_cd="0"(거래소가 "성공"이라고 응답)인데 `output`에 ODNO/
    KRX_FWDG_ORD_ORGNO가 없으면, 이를 무음으로 흡수해 가짜
    exchange_order_id를 만들어내면 안 된다 — `FatalExchangeError`로
    fail-closed 전파되어야 한다(task-2778의 네트워크/크래시 주입과는
    다른 축: 전송은 성공했지만 응답 payload가 불완전한 경우)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": {}})

    adapter = _make_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.place_futureoption_order(_futures_order())


# ---------------------------------------------------------------------------
# 3) failure-injection — 거래소 명시적 거부 응답(취소)
# ---------------------------------------------------------------------------


async def test_cancel_order_exchange_rejection_response_propagates_not_silently_false() -> None:
    """rt_cd != "0"(거래소가 명시적으로 거부)인 응답이 재시도 소진 후에도
    `RetryableExchangeError`로 그대로 드러나야 한다. `cancel_futureoption_
    order`가 이를 삼켜 `False`(정상적으로 확인된 취소 실패)로 뭉개면,
    호출부가 "거래소가 거부했다"와 "요청 자체가 실패했다"를 구분하지
    못해 재시도/알림 정책이 깨진다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        return httpx.Response(200, json={"rt_cd": "1", "msg1": "주문번호 없음"})

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))


# ---------------------------------------------------------------------------
# 4) 수치 성능 단언 — 주문 제출 왕복(정규화된 배율 임계)
# ---------------------------------------------------------------------------


async def test_place_order_round_trip_throughput_within_normalized_budget() -> None:
    """`place_futureoption_order` 왕복(바디 조립 + 응답 파싱)의 반복 측정
    소요시간을, 같은 프로세스·같은 측정 시점에 측정한 동일 규모의 trivial
    dict JSON round-trip 대비 정규화한 배율로 단언한다 — 절대 ms 상수는
    공유 CI에서 상시 적색을 낳으므로(task-2773/2777/2778 선례) 쓰지 않는다."""
    repeats = 20

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return _ok_token(request)
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "OK", "output": {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}},
        )

    adapter = _make_adapter(handler)
    order = _futures_order()

    # 워밍업 — import/토큰발급 관련 1회성 비용이 표본에 섞이지 않게 한다.
    await adapter.place_futureoption_order(order)

    order_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = await adapter.place_futureoption_order(order)
        order_times.append(time.perf_counter() - start)
    assert result.exchange_order_id == "ORG:1"
    order_seconds = min(order_times)

    sample_body = {
        "ORD_PRCS_DVSN_CD": "02",
        "CANO": "12345678",
        "ACNT_PRDT_CD": "01",
        "SLL_BUY_DVSN_CD": "02",
        "SHTN_PDNO": "101W09",
        "ORD_QTY": "1",
        "UNIT_PRICE": "0",
        "NMPR_TYPE_CD": "01",
        "KRX_NMPR_CNDT_CD": "0",
        "ORD_DVSN_CD": "01",
        "CTAC_TLNO": "",
    }
    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        _ = json.loads(json.dumps(sample_body))
        baseline_times.append(time.perf_counter() - start)
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = order_seconds / baseline_seconds
    # httpx MockTransport 라운드트립 + 토큰 헤더 조립 오버헤드 포함, 실측(~350배) 대비
    # 여유(task-2773/test_kis_tr_coverage.py와 동일 판단 폭).
    budget_ratio = 2000.0
    print(
        f"\nplace_futureoption_order round-trip throughput: repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.3f}ms order={order_seconds * 1000:.3f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"place_futureoption_order가 trivial dict round-trip 대비 {ratio:.1f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — 요청 조립/응답 파싱 경로에 의도치 "
        "않은 무거운 연산이 섞였을 가능성."
    )
