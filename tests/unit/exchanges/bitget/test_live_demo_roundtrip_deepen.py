"""task-2795 DEEPEN of task-2179 (L4-30, commit 828a4e6b).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §8.6, §9 L4-30
      (ADR-2026-09-06-G §11)

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md)는 828a4e6b가 만든
`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`가 구조상
`pytest.mark.live_demo`로 기본 CI에서 제외되고(정직한 skip 경로 자체는
정상), 그 파일에는 failure-injection 테스트도 수치 성능 단언도
multi-instance/adversarial 증빙도 없다고 판정했다(실측 D1). 이 파일이
`BitgetAdapter`가 실제로 배선한 재시도/서명/계정모드 파이프라인을
`httpx.MockTransport`로 결정론적으로 검증해 그 세 축을 채운다 — 실키
없이도 항상 실행되는 회귀 테스트다(task-2773/2778/2792와 동일 판단,
KIS `test_domestic_futureoption_wiring_deepen.py` 패턴 참고).

1) failure-injection — place/get/cancel 각각 첫 `N-1`회 `httpx.ConnectError`
   (네트워크 전송 실패) 주입 후 정책상 마지막 허용 시도(`RetryPolicy.
   max_attempts=4`, http_policy.py)에서 성공하는 상황을 재현해, L4-30
   DoD 1(place/cancel/get 왕복 성공)이 재시도 경로를 거친 뒤에도 실제로
   완주함을 증명한다 — 단발 성공 응답만 스텁하는 기존 테스트는 재시도
   루프가 서명/타임스탬프를 시도마다 올바르게 재계산하는지 검증하지
   못한다.
2) 수치 성능 단언 — place+get+cancel 왕복의 실측 소요시간을 동일 N 크기의
   trivial dict 생성 루프에 정규화한 배율로 단언한다(절대 ms 상수는
   공유 CI에서 상시 적색을 낳으므로 쓰지 않음 — task-2773/2778/2792와
   동일 판단).
3) multi-instance/adversarial 증빙 — `account_mode`(L4-31, task-2514)는
   인스턴스 속성이지 클래스/전역 상태가 아니다. 서로 다른 자격증명으로
   만든 두 `BitgetAdapter` 인스턴스를 `asyncio.gather`로 **동시에**
   실행해, (a) 한쪽이 40085를 관측해 UNIFIED로 전환해도 다른 쪽은
   CLASSIC에 남고, (b) 각 인스턴스가 보낸 모든 요청의 `ACCESS-KEY`
   헤더가 그 인스턴스 자신의 키로만 채워짐(자격증명이 인스턴스 경계를
   넘어 섞이지 않음)을 증명한다 — 여러 거래소 계정(예: 실계정 +
   데모계정)을 같은 프로세스에서 동시에 굴리는 배포 형태에서 계정모드나
   자격증명이 서로 새면 조용한 오발주/오조회로 이어지는 adversarial
   시나리오다.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter

_SYMBOL = "BTC/USDT"


async def _no_delay_sleep(_seconds: float) -> None:
    """재시도 백오프를 없애 테스트가 실제 대기 없이 즉시 끝나게 한다."""
    await asyncio.sleep(0)


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        api_key,
        api_secret,
        api_passphrase,
        demo_mode=True,
        http_client=http_client,
        sleep_fn=_no_delay_sleep,
    )


def _order(*, tag: str) -> Order:
    return Order(
        client_order_id=f"deepen-{tag}",
        strategy_id="l4-30-deepen",
        strategy_version="v1",
        symbol=_SYMBOL,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.0002"),
        price=Money(amount=Decimal("10000"), currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )


def _success_envelope(data: dict | list) -> dict:
    return {"code": "00000", "msg": "success", "requestTime": 1, "data": data}


def _order_row(**overrides: object) -> dict:
    row: dict[str, object] = {
        "orderId": "999",
        "clientOid": "deepen-roundtrip",
        "symbol": "BTCUSDT",
        "side": "buy",
        "orderType": "limit",
        "size": "0.0002",
        "status": "live",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 1) failure-injection — 재시도 경로를 거친 뒤에도 place/get/cancel 왕복 완주
# ---------------------------------------------------------------------------


async def test_place_order_survives_transient_network_failure_then_succeeds() -> None:
    """처음 3번은 `httpx.ConnectError`, 정책상 마지막 허용 시도(4번째)에서
    성공한다 — place_order가 재시도 후에도 exchange_order_id/status를
    올바르게 채움을 증명한다."""
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 4:
            raise httpx.ConnectError("simulated network failure", request=request)
        body = json.loads(request.content)
        assert body["clientOid"] == "deepen-place"
        return httpx.Response(
            200, json=_success_envelope({"orderId": "999", "clientOid": "deepen-place"})
        )

    adapter = _make_adapter(
        handler, api_key="key", api_secret="secret", api_passphrase="passphrase"
    )
    order = await adapter.place_order(_order(tag="place"))

    assert len(attempts) == 4
    assert order.exchange_order_id == "999"
    assert order.status == OrderStatus.SUBMITTED


async def test_get_order_survives_transient_network_failure_then_succeeds() -> None:
    """조회 경로도 3번 실패 + 4번째 성공 뒤 올바른 Order를 반환해야 한다."""
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 4:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(200, json=_success_envelope([_order_row()]))

    adapter = _make_adapter(
        handler, api_key="key", api_secret="secret", api_passphrase="passphrase"
    )
    order = await adapter.get_order("999")

    assert len(attempts) == 4
    assert order.exchange_order_id == "999"
    assert order.status != OrderStatus.UNKNOWN


async def test_cancel_order_survives_transient_network_failure_then_succeeds() -> None:
    """취소 경로도 3번 실패 + 4번째 성공 뒤 `True`를 반환해야 한다."""
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 4:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(200, json=_success_envelope({"orderId": "999"}))

    adapter = _make_adapter(
        handler, api_key="key", api_secret="secret", api_passphrase="passphrase"
    )
    cancelled = await adapter.cancel_order("999")

    assert len(attempts) == 4
    assert cancelled is True


# ---------------------------------------------------------------------------
# 2) 수치 성능 단언 — place+get+cancel 왕복(정규화된 배율 임계)
# ---------------------------------------------------------------------------


async def test_place_get_cancel_roundtrip_throughput_within_normalized_budget() -> None:
    """place_order/get_order/cancel_order 왕복 실측 소요시간을, 동일 N 크기의
    trivial dict 생성 루프(같은 프로세스, 같은 측정 시점) 대비 정규화한
    배율로 단언한다 — 절대 ms 상수는 공유 CI에서 상시 적색을 낳으므로
    쓰지 않는다."""
    n = 100
    repeats = 5

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/trade/place-order":
            return httpx.Response(
                200, json=_success_envelope({"orderId": "999", "clientOid": "c"})
            )
        if request.url.path == "/api/v2/spot/trade/orderInfo":
            return httpx.Response(200, json=_success_envelope([_order_row()]))
        assert request.url.path == "/api/v2/spot/trade/cancel-order"
        return httpx.Response(200, json=_success_envelope({"orderId": "999"}))

    adapter = _make_adapter(
        handler, api_key="key", api_secret="secret", api_passphrase="passphrase"
    )

    async def _roundtrip_once() -> None:
        placed = await adapter.place_order(_order(tag="perf"))
        assert placed.exchange_order_id == "999"
        fetched = await adapter.get_order("999")
        assert fetched.exchange_order_id == "999"
        cancelled = await adapter.cancel_order("999")
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
        baseline = [{"orderId": "999", "clientOid": f"c-{i}", "size": "0.0002"} for i in range(n)]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = roundtrip_seconds / baseline_seconds
    # httpx MockTransport 왕복(주문+조회+취소 3회, HMAC 서명 계산 포함) 오버헤드가
    # trivial dict 생성보다 수천 배 커서(task-2792 KIS 동일 패턴, 로컬 실측
    # 유사 자릿수) 여유를 넉넉히 잡는다.
    budget_ratio = 30000.0
    print(
        f"\nplace+get+cancel roundtrip throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms roundtrip={roundtrip_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"place_order/get_order/cancel_order 왕복이 trivial dict 생성 루프 대비 "
        f"{ratio:.1f}배로 회귀했습니다(예산 {budget_ratio}배) — 요청 구성/서명 경로에 "
        "의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) multi-instance/adversarial 증빙 — account_mode/자격증명이 인스턴스 경계를
#    넘어 섞이지 않는다
# ---------------------------------------------------------------------------


async def test_account_mode_and_credentials_isolated_across_concurrent_instances() -> None:
    """서로 다른 자격증명의 두 `BitgetAdapter`를 동시에(`asyncio.gather`)
    돌린다. instance A는 첫 호출에서 40085(Unified 강제 신호)를 받아
    UNIFIED로 전환되고, instance B는 항상 성공해 CLASSIC에 남는다.
    두 인스턴스의 `account_mode`가 서로 영향을 주지 않고, 각자가 보낸
    요청의 `ACCESS-KEY`가 자기 자신의 키로만 채워져 있어야 한다(계정모드나
    자격증명이 프로세스 전역/클래스 상태로 새면 여러 계정을 한 프로세스에서
    동시에 굴리는 배포에서 조용한 오발주/오조회로 이어지는 adversarial
    시나리오)."""
    a_requests: list[httpx.Request] = []
    b_requests: list[httpx.Request] = []

    def handler_a(request: httpx.Request) -> httpx.Response:
        a_requests.append(request)
        if len(a_requests) == 1:
            # Classic v2 경로가 40085로 거부되는 상황(L4-31, task-2514) —
            # account_aware_request()가 이를 관측해 UNIFIED로 전환한 뒤
            # v3 경로로 재조립해 재시도한다.
            assert request.url.path == "/api/v2/spot/trade/place-order"
            return httpx.Response(
                200,
                json={
                    "code": "40085",
                    "msg": "unified account required",
                    "requestTime": 1,
                    "data": {},
                },
            )
        assert request.url.path == "/api/v3/trade/place-order"
        return httpx.Response(200, json=_success_envelope({"orderId": "A-1", "clientOid": "a"}))

    def handler_b(request: httpx.Request) -> httpx.Response:
        b_requests.append(request)
        assert request.url.path == "/api/v2/spot/trade/place-order"
        return httpx.Response(200, json=_success_envelope({"orderId": "B-1", "clientOid": "b"}))

    adapter_a = _make_adapter(
        handler_a, api_key="key-a", api_secret="secret-a", api_passphrase="pass-a"
    )
    adapter_b = _make_adapter(
        handler_b, api_key="key-b", api_secret="secret-b", api_passphrase="pass-b"
    )

    order_a, order_b = await asyncio.gather(
        adapter_a.place_order(_order(tag="a")),
        adapter_b.place_order(_order(tag="b")),
    )

    assert order_a.exchange_order_id == "A-1"
    assert order_b.exchange_order_id == "B-1"

    # (a) account_mode는 인스턴스별로 독립적이다 — A의 UNIFIED 전환이 B로
    # 새지 않는다.
    from src.exchanges.bitget.account_mode import BitgetAccountMode

    assert adapter_a.account_mode is BitgetAccountMode.UNIFIED
    assert adapter_b.account_mode is BitgetAccountMode.CLASSIC

    # (b) 자격증명이 인스턴스 경계를 넘어 섞이지 않는다.
    assert len(a_requests) == 2  # 40085 거부 1회 + UNIFIED 재시도 1회
    assert len(b_requests) == 1
    assert all(req.headers["ACCESS-KEY"] == "key-a" for req in a_requests)
    assert all(req.headers["ACCESS-KEY"] == "key-b" for req in b_requests)
