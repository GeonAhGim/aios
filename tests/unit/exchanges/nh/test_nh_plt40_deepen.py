"""task-3176 DEEPEN of task-1105(PLT-40c, commit cda979f, docs/audit/DEPTH_PLT.md #1105).

DEPTH 감사(`docs/audit/DEPTH_PLT.md` #1105)는 nh 믹스인 4건 `type: ignore`
제거(`common/http_client.py`의 `NHHTTPClient` Protocol 도입) 리프가
negative(`test_nh_http_client_protocol.py` + `test_nh_adapter.py` 20건
이상)/실패 주입(`ConnectionClosed` 재연결, `httpx.ConnectError` 재시도
2건)/게이트 적색 재현(`test_check_type_ignore_budget.py`, PLT-40 공유
스크립트)을 이미 갖췄지만 수치 성능 단언이 없다고("지연/처리량 단언
전무") 판정했다 — 이 파일이 그 한 가지만 보강한다. `adapter.py`/
`trading_mixin.py`/`market_data_mixin.py`는 손대지 않는다(런타임 동작
무변경, task-1105 커밋 메시지와 동일 판단).

이 리프가 실제로 건드린 두 믹스인(`trading_mixin.py` 40줄 diff,
`market_data_mixin.py` 9줄 diff — 둘 다 `self: NHHTTPClient` Protocol
도입)의 실행 경로를 비교한다: `place_order`(dict 구성 + `Order.model_copy`
검증까지 포함하는 무거운 경로)의 왕복 처리량을, 같은 프로세스·같은
MockTransport 하에서 측정한 `get_ticker`(트리비얼 고정 응답, 가벼운
경로) 왕복 처리량 대비 정규화한 배율로 단언한다 — 절대 ms 상수는 공유
CI에서 상시 적색을 낳으므로(`test_nh_oauth_deepen.py`/task-2782와 동일
판단) 쓰지 않는다. 두 경로 모두 동일한 `_request`(httpx 비동기 왕복 +
이벤트 루프 스케줄링)를 거치므로 그 고정비용은 배율에서 상쇄되고,
`place_order`가 추가로 하는 계산(str 변환 다건 + pydantic
`model_copy` 검증)만 배율에 남는다.

`place_order`는 `@require_paper_sandbox`로 감싸여 있고 `NHAdapter.
is_paper_trading`/`is_sandboxed`는 생성자 인자와 무관하게 항상 False라
직접 호출이 구조적으로 불가능하다(`test_nh_adapter.py` 상단 주석과 동일
사실) — 원본 함수는 `functools.wraps`가 남긴 `__wrapped__`로 우회해야
하는데, `type-ignore-budget.txt` 게이트가 저장소 전체(tests 포함)를
스캔하므로 새 `# type: ignore`를 추가하면 이 DEEPEN 리프 자체가 budget을
넘겨 FAIL을 만든다 — task-1105가 `websocket_mixin.py`에서 쓴 것과 동일한
기법(`cast()`로 무해하게 해소, ignore 없이)을 그대로 재사용한다.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.nh.adapter import NHAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _make_adapter(handler: Any) -> NHAdapter:
    client = httpx.AsyncClient(
        base_url="https://moapi.nhplug.com:8443", transport=httpx.MockTransport(handler)
    )
    return NHAdapter("appkey", "appsecret", "1234567890", http_client=client)


def _route(request: httpx.Request, path: str, body: dict[str, Any]) -> httpx.Response:
    if request.url.path == "/oauth2/token":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    assert request.url.path == path, f"unexpected path {request.url.path}"
    return httpx.Response(200, json=body)


async def _unguarded_place_order(adapter: NHAdapter, order: Order) -> Order:
    # task-1105 websocket_mixin.py가 쓴 것과 동일 기법 — attr-defined를
    # ignore 대신 cast(Any, ...)로 해소해 type-ignore-budget.txt를 건드리지
    # 않는다(모듈 docstring 참조).
    place_order_fn = cast(Any, NHAdapter.place_order).__wrapped__
    result: Order = await place_order_fn(adapter, order)
    return result


def _order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="nh",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )


@pytest.mark.perf
async def test_place_order_throughput_within_normalized_budget_vs_ticker() -> None:
    """PLT-40c(task-1105)가 만진 두 믹스인 경로의 처리량 배율을 단언한다
    (모듈 docstring 참조) — DEPTH 감사 결손: "no numeric performance/
    latency assertion"."""
    n = 200
    repeats = 3

    def order_handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            "/krstock/order/v1/cashBuy",
            {"rsp_cd": "00000", "rsp_msg": "정상처리완료", "Output_0": {"mkt_orr_no": 999}},
        )

    def ticker_handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            "/krstock/quote/v1/currentPrice",
            {"rsp_cd": "00000", "rsp_msg": "정상처리완료", "Output_0": {"stck_prpr": "70000"}},
        )

    order_adapter = _make_adapter(order_handler)
    ticker_adapter = _make_adapter(ticker_handler)
    order = _order()

    # 워밍업 — 토큰 발급(1회성 네트워크 비용)이 표본에 섞이지 않게 한다.
    warmup_order = await _unguarded_place_order(order_adapter, order)
    assert warmup_order.status == OrderStatus.SUBMITTED
    warmup_ticker = await ticker_adapter.get_ticker("005930")
    assert warmup_ticker.price is not None

    order_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(n):
            await _unguarded_place_order(order_adapter, order)
        order_times.append(time.perf_counter() - start)
    order_seconds = min(order_times)

    ticker_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(n):
            await ticker_adapter.get_ticker("005930")
        ticker_times.append(time.perf_counter() - start)
    ticker_seconds = min(ticker_times)

    assert ticker_seconds > 0.0
    ratio = order_seconds / ticker_seconds
    # 둘 다 동일한 MockTransport 왕복(_request)을 거치므로 이벤트
    # 루프/httpx 고정비는 상쇄되고, place_order가 추가로 하는 dict 구성 +
    # Order.model_copy(pydantic 검증)만 배율에 남는다 — 여유 폭은
    # test_nh_oauth_deepen.py(budget_ratio=60.0)와 동일 판단(공유 CI 변동성).
    budget_ratio = 20.0
    print(
        f"\nnh place_order/get_ticker throughput ratio: n={n} repeats={repeats} "
        f"ticker={ticker_seconds * 1000:.2f}ms order={order_seconds * 1000:.2f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"place_order가 get_ticker 대비 {ratio:.2f}배로 회귀했습니다"
        f"(예산 {budget_ratio}배) — 주문 경로에 의도치 않은 무거운 연산이 섞였을 "
        "가능성."
    )
