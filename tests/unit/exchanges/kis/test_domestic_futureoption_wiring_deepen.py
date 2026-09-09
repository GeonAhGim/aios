"""task-2792 DEEPEN of task-2008 (BR-6 DoD(b), commit f816fbbe).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md)는 f816fbbe가 ORD_DVSN_CD
(주문)·RMN_QTY_YN(취소) 배선을 증명하는 negative test 2개(gate-red 회귀
용도)만 추가했고, 그 두 테스트 자체에는 failure-injection과 수치 성능
단언이 없다고 판정했다(실측 D1, D2 하한 미달). task-2778(commit 8b694f36,
test_domestic_futureoption_deepen.py)이 이미 place/cancel에 대한
failure-injection(네트워크/크래시)과 balance 파싱에 대한 수치 성능 단언을
추가했지만, 그 numeric perf는 ORD_DVSN_CD/RMN_QTY_YN을 담는 place/cancel
경로 자체를 재지 않는다 — 이 파일이 그 두 축(f816fbbe가 직접 건드린
place/cancel 경로에 한정된 failure-injection과 numeric perf)을 마무리한다.
`domestic_futureoption_mixin.py`는 손대지 않는다(DoD(a)(c)(d) 기존 APPROVE
범위 밖).

1) failure-injection + 배선 결합 — 재시도 정책(`RetryPolicy.max_attempts=4`,
   http_policy.py)이 첫 시도들에서 `httpx.ConnectError`를 겪은 뒤 마지막
   시도에서 성공하는 상황을 주입해, 재시도를 거친 뒤에도 ORD_DVSN_CD/
   RMN_QTY_YN이 여전히 요청 바디에 살아있음을 증명한다 — 재시도 경로가
   요청 바디를 다시 만들면서 이 필드를 빠뜨리는 회귀는 필드 존재만 보는
   단발성 테스트로는 잡히지 않는다.
2) 수치 성능 단언 — `place_futureoption_order`/`cancel_futureoption_order`가
   ORD_DVSN_CD/RMN_QTY_YN을 포함한 요청 바디를 구성해 왕복하는 실측
   소요시간을, 동일 프로세스에서 측정한 동일 N 크기의 trivial dict 생성
   루프에 정규화한 배율로 단언한다(절대 ms 상수 대신 — task-2773/2777/2778과
   동일 판단).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter

_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_CANCEL_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"


async def _no_delay_sleep(_seconds: float) -> None:
    """재시도 백오프를 없애 테스트가 실제 대기 없이 즉시 끝나게 한다."""
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
# 1) failure-injection + 배선 결합 — 재시도 후에도 필드가 살아있음을 증명
# ---------------------------------------------------------------------------


async def test_place_order_ord_dvsn_cd_survives_retry_after_transient_failure() -> None:
    """처음 3번은 `httpx.ConnectError`(네트워크 실패주입), 4번째(정책상 마지막
    허용 시도)에 성공하는 상황에서도, 최종적으로 성공한 요청 바디에
    ORD_DVSN_CD가 여전히 담겨 있어야 한다 — 재시도 경로가 바디를 다시
    만들면서 DoD(b) 필드를 빠뜨리는 회귀는 단발 성공 테스트로는 못 잡는다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        captured.append(request)
        if len(captured) < 4:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "OK",
                "output": {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"},
            },
        )

    adapter = _make_adapter(handler)

    order = await adapter.place_futureoption_order(_futures_order())

    assert order.exchange_order_id == "ORG:1"
    assert len(captured) == 4  # 3번 실패 + 마지막 성공
    order_body = json.loads(captured[-1].content)
    assert "ORD_DVSN_CD" in order_body
    assert order_body["ORD_DVSN_CD"] == "01"


async def test_cancel_order_rmn_qty_yn_survives_retry_after_transient_failure() -> None:
    """취소 경로도 동일하게 3번 실패 + 4번째 성공 뒤, 최종 성공 요청 바디에
    RMN_QTY_YN이 살아있어야 한다."""
    captured: list[httpx.Request] = []
    cancel_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        nonlocal cancel_attempts
        cancel_attempts += 1
        captured.append(request)
        if cancel_attempts < 4:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": {}})

    adapter = _make_adapter(handler)

    cancelled = await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))

    assert cancelled is True
    assert cancel_attempts == 4
    cancel_body = json.loads(captured[-1].content)
    assert "RMN_QTY_YN" in cancel_body
    assert cancel_body["RMN_QTY_YN"] == "N"


# ---------------------------------------------------------------------------
# 2) 수치 성능 단언 — place/cancel 요청 왕복(정규화된 배율 임계)
# ---------------------------------------------------------------------------


async def test_place_and_cancel_order_roundtrip_throughput_within_normalized_budget() -> None:
    """`place_futureoption_order`(ORD_DVSN_CD 포함)와 `cancel_futureoption_order`
    (RMN_QTY_YN 포함)를 반복 호출하는 실측 소요시간을, 동일 N 크기의 trivial
    dict 생성 루프(같은 프로세스, 같은 측정 시점) 대비 정규화한 배율로
    단언한다 — 절대 ms 상수는 공유 CI에서 상시 적색을 낳으므로 쓰지 않는다."""
    n = 200
    repeats = 5

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200, json={"access_token": "t", "access_token_token_expired": ""}
            )
        if request.url.path == _ORDER_PATH:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            output = {}
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    adapter = _make_adapter(handler)

    async def _roundtrip_once() -> None:
        order = await adapter.place_futureoption_order(_futures_order())
        assert order.exchange_order_id == "ORG:1"
        cancelled = await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))
        assert cancelled is True

    # 워밍업 — import/JIT 관련 1회성 비용이 표본에 섞이지 않게 한다.
    await _roundtrip_once()

    roundtrip_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(n):
            await _roundtrip_once()
        roundtrip_times.append(time.perf_counter() - start)
    roundtrip_seconds = min(roundtrip_times)

    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        baseline = [
            {"ORD_DVSN_CD": "01", "RMN_QTY_YN": "N", "SHTN_PDNO": f"10{i:05d}"}
            for i in range(n)
        ]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = roundtrip_seconds / baseline_seconds
    # httpx MockTransport 왕복(주문+취소 2회, 서명/OAuth 헤더 계산 포함) 오버헤드가
    # trivial dict 생성보다 수천 배 커서(로컬 실측 ~8,400배) 절대 임계와 마찬가지로
    # 여유를 넉넉히 잡는다 — task-2773/test_kis_tr_coverage.py와 동일 판단이지만
    # 이 경로는 OAuth 헤더 계산까지 포함해 배율 자체가 훨씬 크다.
    budget_ratio = 30000.0
    print(
        f"\nplace+cancel roundtrip throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms roundtrip={roundtrip_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"place_futureoption_order/cancel_futureoption_order 왕복이 trivial dict 생성 "
        f"루프 대비 {ratio:.1f}배로 회귀했습니다(예산 {budget_ratio}배) — 요청 구성 "
        "경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )
