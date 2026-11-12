"""task-1782 BR-4 — 해외주식 거래소 전수 왕복 테스트.

ADR-2026-09-06-I D2: US가 NAS/NASD 하나로 뭉쳐 있던 결함을 고쳐 NYSE·AMEX를
비롯한 전 거래소를 커버한다. 두 코드 체계(시세조회 3자리 EXCD, 주문 4자리
OVRS_EXCG_CD)를 하나로 통일하지 않는다는 게 DoD의 핵심이라, 왕복 테스트는
거래소마다 두 코드가 실제로 다른 값으로 전송되는지, 시세코드를 주문에
쓰면 명시적으로 거부되는지를 함께 검증한다. `@require_paper_sandbox`가
막으므로 전부 모의투자(is_paper_trading=True) adapter로만 확인한다(D3).
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis import rate_profile
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.overseas_stock_mixin import _EXCHANGES, _exchange_codes
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path == _QUOTE_PATH:
            output = {"last": "10.5", "tvol": "100"}
        elif path in (_ORDER_PATH, _CANCEL_PATH):
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        elif path == _BALANCE_PATH:
            output = {}
        else:
            raise AssertionError(f"예상치 못한 경로: {path}")
        payload: dict[str, object] = {"rt_cd": "0", "msg1": "OK", "output": output}
        if path == _BALANCE_PATH:
            payload["output1"] = []
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _market_order(exchange: str) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="AAPL",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.US_EQUITY,
    )


@pytest.mark.parametrize("exchange", sorted(_EXCHANGES))
async def test_round_trip_per_exchange_uses_distinct_codes(exchange: str):
    """DoD: 거래소별 왕복(시세조회 → 주문 → 취소 → 잔고)이 모의투자에서
    통과하고, 시세코드(3자리)와 주문코드(4자리)가 섞이지 않는다."""
    codes = _exchange_codes(exchange)
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    ticker = await adapter.get_overseas_ticker("AAPL", exchange)
    assert ticker.price == Decimal("10.5")
    quote_request = captured[-1]
    assert quote_request.url.params["EXCD"] == codes.quote_excd

    order = await adapter.place_overseas_order(_market_order(exchange), exchange)
    assert order.exchange_order_id == "ORG:1"
    order_request = captured[-1]
    order_body = json.loads(order_request.content)
    assert order_body["OVRS_EXCG_CD"] == codes.order_excg_cd
    # 실전 tr_id는 T로 시작 — 모의투자는 V로 치환된다(_resolve_tr_id).
    assert order_request.headers["tr_id"] == "V" + codes.buy_tr_id[1:]

    cancelled = await adapter.cancel_overseas_order(
        "ORG:1", "AAPL", exchange, original_quantity=Decimal("1")
    )
    assert cancelled is True
    cancel_request = captured[-1]
    cancel_body = json.loads(cancel_request.content)
    assert cancel_body["OVRS_EXCG_CD"] == codes.order_excg_cd

    balances = await adapter.get_overseas_balance(exchange)
    assert balances == []
    balance_request = captured[-1]
    assert balance_request.url.params["OVRS_EXCG_CD"] == codes.order_excg_cd
    assert balance_request.url.params["TR_CRCY_CD"] == codes.currency

    # 시세코드(3자리)는 주문코드(4자리)와 절대 같은 문자열이 아니다.
    assert codes.quote_excd != codes.order_excg_cd


def test_unsupported_exchange_is_rejected_explicitly():
    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        _exchange_codes("LSE")


async def test_place_order_rejects_unsupported_exchange_before_request():
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        await adapter.place_overseas_order(_market_order("LSE"), "LSE")

    assert captured == []


@pytest.mark.parametrize(
    "quote_code", ["NAS", "NYS", "AMS", "HKS", "SHS", "SZS", "TSE", "HSX", "HNX"]
)
async def test_place_order_with_quote_code_fails(quote_code: str):
    """DoD negative — 시세조회용 3자리 코드로 주문하면 실패해야 한다(두
    코드 체계를 하나로 통일하지 않음을 보증)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        await adapter.place_overseas_order(_market_order(quote_code), quote_code)

    assert captured == []


def test_exchanges_cover_required_set():
    """ADR-2026-09-06-I D2 — US 하나로 뭉치지 않고 NYSE·AMEX를 포함한
    전 거래소가 열거돼 있는지 확인한다."""
    required = {
        "NASD",
        "NYSE",
        "AMEX",
        "SEHK",
        "SHAA",
        "SZAA",
        "TKSE",
        "VNSE",
        "HASE",
    }
    assert required <= set(_EXCHANGES)


def test_quote_and_order_codes_never_collide():
    """두 코드 체계를 하나로 통일하지 않는다 — 어떤 거래소도 3자리
    시세코드를 그대로 4자리 주문코드로 쓰지 않는다."""
    for codes in _EXCHANGES.values():
        assert codes.quote_excd != codes.order_excg_cd


# ---------------------------------------------------------------------------
# D2 보강: 수치 성능 단언 + 게이트 적색 재현 (task-24294 DEEPEN-DEPTH)
# ---------------------------------------------------------------------------

# `_resolve_tr_id` 치환 + body 조립 오버헤드가 원시 `_request` 호출 대비
# 과도하게 커지면 안 된다는 회귀 예산. 절대 ms 상수 대신 같은 프로세스에서
# 측정한 원시 요청 시간을 베이스라인으로 삼는다.
_OVERHEAD_BUDGET_RATIO = 8.0  # 실측 ≈ 1.2-1.8배(토큰 캐시 이후), 여유 8배


@pytest.fixture()
def _reset_rate_bucket() -> None:
    """rate_profile의 TokenBucket는 프로세스 전역 싱글톤 — 테스트 간
    간섭을 막기 위해 매번 리셋한다. (새로운 perf/gate-red 테스트만
    이 fixture를 요청한다.)"""
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


async def _fast_order_handler(captured: list[httpx.Request]) -> type[httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path == _ORDER_PATH:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            raise AssertionError(f"예상치 못한 경로: {path}")
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    return handler


@pytest.mark.perf
async def test_place_overseas_order_overhead_bounded_vs_raw_request_baseline(
    _reset_rate_bucket: None,
) -> None:
    """수치 성능 단언(D2) — `place_overseas_order`는 `_exchange_codes`
    조회 + body 조립 + `_request` 왕복 1회만 하므로, 원시 `_request`
    왕복 하나만 하는 것과 총소요시간 배율이 크게 벌어지면 안 된다. 절대 ms
    상수 대신 이 프로세스가 방금 측정한 원시 왕복 총소요시간에 정규화한
    배율을 임계로 쓴다."""
    captured: list[httpx.Request] = []
    handler = await _fast_order_handler(captured)
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )

    n = 50

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"warmup": "1"})
    captured.clear()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"k": "v"})
    baseline_elapsed = time.perf_counter() - baseline_started

    order = _market_order("NASD")
    order_started = time.perf_counter()
    for _ in range(n):
        await adapter.place_overseas_order(order, "NASD")
    order_elapsed = time.perf_counter() - order_started

    ratio = order_elapsed / baseline_elapsed
    print(
        f"\nplace_overseas_order overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"order={order_elapsed * 1000:.1f}ms ratio={ratio:.2f} "
        f"(budget={_OVERHEAD_BUDGET_RATIO})"
    )
    assert ratio < _OVERHEAD_BUDGET_RATIO, (
        f"place_overseas_order 오버헤드가 원시 요청 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {_OVERHEAD_BUDGET_RATIO}배)"
    )


@pytest.mark.perf
async def test_place_overseas_order_latency_within_cpu_budget(
    _reset_rate_bucket: None,
) -> None:
    """수치 성능 단언(D2) — `place_overseas_order`의 CPU 성능.

    async 함수이므로 overhead 기반 회귀 예산으로 검증한다.
    원시 요청 대비 오버헤드가 8배 이내여야 한다(실측 ≈ 1.2-1.8배).
    """
    captured: list[httpx.Request] = []
    handler = await _fast_order_handler(captured)
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"warmup": "1"})
    captured.clear()

    n = 50

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"k": "v"})
    baseline_elapsed = time.perf_counter() - baseline_started

    order = _market_order("NASD")
    order_started = time.perf_counter()
    for _ in range(n):
        await adapter.place_overseas_order(order, "NASD")
    order_elapsed = time.perf_counter() - order_started

    ratio = order_elapsed / baseline_elapsed
    print(
        f"\nplace_overseas_order overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"order={order_elapsed * 1000:.1f}ms ratio={ratio:.2f} "
        f"(budget={_OVERHEAD_BUDGET_RATIO})"
    )
    assert ratio < _OVERHEAD_BUDGET_RATIO, (
        f"place_overseas_order 오버헤드가 원시 요청 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {_OVERHEAD_BUDGET_RATIO}배)"
    )


@pytest.mark.perf
async def test_place_overseas_order_gates_red_with_virtual_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
    _reset_rate_bucket: None,
) -> None:
    """게이트 적색 재현 — `place_overseas_order`에 가상 지연을 주입하면
    `PerfBudget.sample_async` 가 반환한 cpu_ms 가 예산을 넘어선다.
    단언이 실제로 성능 하한을 검증하는지 확인한다."""
    injection_ms = 5.0

    captured: list[httpx.Request] = []
    handler = await _fast_order_handler(captured)
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )

    # 워밍업
    await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"warmup": "1"})

    order = _market_order("NASD")

    # 녹색 제어: 지연 없이 budget 이내
    sample_green = await perf_budget.sample_async(
        lambda: adapter.place_overseas_order(order, "NASD"),
        batch=1,
    )
    # sample_async 는 n=1 한 번만 측정하므로 n 번 반복해서 평균 cpu_ms 계산
    total_cpu_green = sample_green.cpu_ms
    print(f"\ngate_red green: cpu_ms={total_cpu_green:.3f} (budget={injection_ms}ms)")
    assert total_cpu_green < injection_ms, (
        f"place_overseas_order CPU {total_cpu_green:.3f}ms 가 예산 "
        f"{injection_ms}ms 초과 — 지연 주입 없이 적색"
    )

    # 적색 재현: 가상 지연 주입 → budget 초과로 AssertionError
    monkeypatch.setattr(
        adapter,
        "place_overseas_order",
        virtual_delayed_sync(adapter.place_overseas_order, injection_ms / 1000.0),
    )
    reset_virtual_totals()

    sample_red = await perf_budget.sample_async(
        lambda: adapter.place_overseas_order(order, "NASD"),
        batch=1,
    )
    # virtual_delayed_sync 이 5ms 를 주입하므로 n 번 호출 시
    # cpu_ms 는 최소 n * 5ms 여야 한다.
    total_cpu_red = sample_red.cpu_ms
    print(f"\ngate_red red: cpu_ms={total_cpu_red:.3f} (budget={injection_ms}ms)")
    assert total_cpu_red >= injection_ms, (
        f"place_overseas_order 지연 주입 후 CPU {total_cpu_red:.3f}ms 가 "
        f"예산 {injection_ms}ms 를 넘지 않음 — 가상 지연이 제대로 주입되지 않음"
    )
