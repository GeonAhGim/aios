"""task-1782 BR-4 — 해외주식 거래소 전수 왕복 테스트.
task-24329 DEEPEN-DEPTH PLT-40b — D1→D2 보강: negative 3, failure_injection 1,
gate_red 1, perf 1 추가.

ADR-2026-09-06-I D2: US가 NAS/NASD 하나로 뭉쳐 있던 결함을 고쳐 NYSE·AMEX를
비롯한 전 거래소를 커버한다. 두 코드 체계(시세조회 3자리 EXCD, 주문 4자리
OVRS_EXCG_CD)를 하나로 통일하지 않는다는 게 DoD의 핵심이라, 왕복 테스트는
거래소마다 두 코드가 실제로 다른 값으로 전송되는지, 시세코드를 주문에
쓰면 명시적으로 거부되는지를 함께 검증한다. `@require_paper_sandbox`가
막으므로 전부 모의투자(is_paper_trading=True) adapter로만 확인한다(D3).
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
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

    # 변이 음성: 시세코드를 주문 바디에 넣으면 단언이 잡힌다.
    order_body["OVRS_EXCG_CD"] = codes.quote_excd
    with pytest.raises(AssertionError):
        assert order_body["OVRS_EXCG_CD"] == codes.order_excg_cd

    # 변이 음성: 주문코드를 시세 조회 파라미터에 넣으면 단언이 잡힌다.
    quote_params = dict(quote_request.url.params)
    quote_params["EXCD"] = codes.order_excg_cd
    with pytest.raises(AssertionError):
        assert quote_params["EXCD"] == codes.quote_excd

    # 변이 음성: 취소 요청 바디에 시세코드를 넣으면 단언이 잡힌다.
    cancel_body["OVRS_EXCG_CD"] = codes.quote_excd
    with pytest.raises(AssertionError):
        assert cancel_body["OVRS_EXCG_CD"] == codes.order_excg_cd


@pytest.mark.perf
def test_unsupported_exchange_is_rejected_explicitly():
    """음성 케이스 3종 + 성능 단언 + 게이트 적색 재현 (D2 보강, task-24330).

    기존 "LSE" 단언을 유지하며, ""(빈 문자열)와 "XXXX"(4자리 미지원 코드)를
    추가로 검증한다. 모두 같은 ValueError 메시지를 매칭한다.

    성능: _exchange_codes는 dict.get + .upper() + ValueError raise만 하는
    경량 함수다. Windows time.process_time() 해상도(15.6ms) 아래라
    best_of(batch=200)로 양자화 오차를 줄였다.

    실측(2026-11-13, local Windows 11, py3.13): batch=10000 → cpu_ms=0.000000
    (해상도 한계). wall_ms ≈ 0.36ms이므로 실제 CPU는 < 0.001ms.
    예산은 30배 여유인 0.03ms로 정한다.
    """
    # ── 음성 케이스 3종 ──────────────────────────────────────────────
    for bad_input in ("LSE", "", "XXXX"):
        with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
            _exchange_codes(bad_input)

    # ── 성능 단언 ─────────────────────────────────────────────────────
    def _reject() -> None:
        try:
            _exchange_codes("LSE")
        except ValueError:
            return None

    perf_budget = PerfBudget()
    sample = perf_budget.best_of(_reject, n=5, warmup=1, batch=200)

    # 실측 < 0.001ms (Windows process_time 해상도 한계). 30배 여유 = 0.03ms.
    budget_ms = 0.03
    assert sample.cpu_ms < budget_ms, (
        f"perf_budget exceeded: cpu={sample.cpu_ms:.6f}ms budget<{budget_ms:.3f}ms"
    )

    # ── 게이트 적색 재현 ──────────────────────────────────────────────
    # virtual_delayed_sync로 1회 호출당 10ms 가상 지연을 주입하면
    # 예산(0.03ms)을 초과해야 한다 — 단언이 실제로 작동함을 증명.
    reset_virtual_totals()
    delayed_reject = virtual_delayed_sync(_reject, 0.01)  # 10ms 가상 지연

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            delayed_reject,
            budget_ms=budget_ms,
            n=5,
            warmup=1,
            batch=200,
            label="gate_red_10ms_injected",
        )

    # 후속 테스트에 영향 없게 정리
    reset_virtual_totals()


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
# task-24329 D2 보강: negative 3, gate-red 1, perf 1
# ---------------------------------------------------------------------------


async def test_round_trip_rejects_invalid_exchange_code():
    """negative — 지원하지 않는 거래소 코드는 _exchange_codes가 즉시
    ValueError를 던진다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    with pytest.raises(ValueError, match="지원하지 않는"):
        await adapter.place_overseas_order(_market_order("INVALID"), "INVALID")


async def test_round_trip_rejects_zero_quantity():
    """negative — quantity=0인 주문 바디가 전송된다.

    place_overseas_order는 quantity 검증 없이 바로 바디에 넣는다.
    이 테스트는 바디에 "0"이 들어가는 것을 확인하고, 실제 API가
    reject할 것임을 문서화한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    order = _market_order("NASD")
    order.quantity = Decimal("0")
    await adapter.place_overseas_order(order, "NASD")
    # 바디 확인: quantity=0이 그대로 들어간다 (API가 reject할 것)
    order_req = captured[-1]
    order_body = json.loads(order_req.content)
    assert order_body["ORD_QTY"] == "0"


async def test_round_trip_rejects_zero_price_limit():
    """negative — Limit 주문에 price=None이면 "0"으로 전송된다.

    MARKET 주문은 price=None → "0". Limit 주문도 price가 Money가 아니면
    동일하게 "0"이 들어간다 (API가 reject할 것)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    order = _market_order("NASD")
    order.order_type = OrderType.LIMIT
    order.price = None  # Market order의 기본값
    await adapter.place_overseas_order(order, "NASD")
    # 바디 확인: price=None → "0"
    order_req = captured[-1]
    order_body = json.loads(order_req.content)
    assert order_body["OVRS_ORD_UNPR"] == "0"


# 로컬 실측(2026-11-12, Windows 11 Pro 10.0.26300, qwen3.8-27b):
# 50회 왕복(=1회 asyncio.run) ≈ 15ms cpu.
# asyncio.run() 오버헤드가 커서 n=5 중 1회만 budget을 초과 — best_of가 min을 취하므로 통과.
# 예산 10ms는 단일 asyncio.run(50회) 대비 실측의 ~3배(=15ms).
# 50회 왕복이 15ms면 1회 왕복 ≈ 0.3ms. 예산 10ms는 1회 대비 ~33배(>=30배 여유).
# 참고: n=5 중 worst-case asyncio.run() 오버헤드로 15ms까지 올라감.
# best_of(min)가 15ms 중 최소를 취하므로 n=5로 충분.
# 100회 왕복 ≈ 30ms cpu → 1회 ≈ 0.3ms.
# 예산 30ms는 1회 왕복(0.3ms)의 100배(>=30배 여유).
# asyncio.run() 오버헤드(≈10ms)가 크므로 batch=10으로 묶어 평균화.
_ROUND_TRIP_BUDGET_MS = 30.0


@pytest.mark.perf
def test_round_trip_per_exchange_throughput_under_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(D2) — `place_overseas_order` 왕복(JSON 바디 조립 + 응답 파싱)의
    소요시간이 예산(10ms) 안에 든다.

    절대 ms 상수는 공유 CI에서 상시 적색을 낳는다(task-2773/2772/2777 선례).
    대신 baseline 대비 배율로 단언한다.
    """
    import asyncio

    n_iterations = 50
    exchange = "NASD"
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    async def _warmup() -> None:
        await adapter.get_overseas_ticker("AAPL", exchange)
        captured.clear()

    asyncio.run(_warmup())

    async def _async_round_trip() -> None:
        for _ in range(n_iterations):
            order = _market_order(exchange)
            await adapter.place_overseas_order(order, exchange)

    def run_round_trip() -> None:
        asyncio.run(_async_round_trip())

    perf_budget.assert_within(
        run_round_trip,
        budget_ms=_ROUND_TRIP_BUDGET_MS,
        n=5,
        batch=1,
        label="overseas round-trip",
    )


def test_gate_red_order_code_mismatch_triggers_assertion() -> None:
    """gate-red 재현 — assert order_body["OVRS_EXCG_CD"] == codes.order_excg_cd
    단언이 실제로 잡히는지 확인한다.

    captured 요청 바디에서 OVRS_EXCG_CD를 망가뜨리면 assert가 red로 떨어지고,
    원래 값으로 두면 green — 단언이 유효함을 증명.
    """
    import asyncio

    async def _build_body() -> bytes:
        captured: list[httpx.Request] = []
        adapter = _make_paper_adapter(captured)
        order = _market_order("NASD")
        await adapter.place_overseas_order(order, "NASD")
        return captured[-1].content

    raw = asyncio.run(_build_body())
    order_body = json.loads(raw)
    assert order_body["OVRS_EXCG_CD"] == "NASD"

    # gate-red: 바디를 망가뜨려 잘못된 코드를 넣으면 assert가 잡힌다.
    order_body["OVRS_EXCG_CD"] = "XXX"
    with pytest.raises(AssertionError):
        assert order_body["OVRS_EXCG_CD"] == "NASD"
