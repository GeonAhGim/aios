"""task-3161 DEEPEN of task-1032 (PLT-40a 선행) — `futures_trading_mixin.py`/
`market_data_mixin.py`/`trading_mixin.py`/`adapter.py` 순수 리팩터(735/357/
314줄 파일을 P6 line_cap 준수를 위해 분할, 동작 변경 0 명시).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-40

원 리프는 "순수 이동만"을 근거로 새 테스트를 추가하지 않았고, 분할 이후에도
이 네 파일을 직접 겨냥한 D2 증빙(negative>=3/실패주입 1/수치 성능단언 1/
게이트 적색재현 1)이 없었다. 기존 해피패스 커버리지
(`tests/integration/test_bitget_futures.py`, `tests/integration/
test_bitget_adapter.py`)는 건드리지 않고 증빙만 보강한다 — 새 기능 추가나
동작 변경 없음.
"""

from __future__ import annotations

import ast
import asyncio
import time
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from tests.unit.exchanges.test_live_guard_coverage import (
    _has_guard_decorator,
    _is_stub_body,
)


async def _no_delay_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _make_adapter(handler: Callable[[httpx.Request], httpx.Response]) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key",
        "secret",
        "passphrase",
        demo_mode=True,
        http_client=http_client,
        sleep_fn=_no_delay_sleep,
    )


def _envelope(code: str, data: object) -> dict[str, object]:
    return {"code": code, "msg": "test", "requestTime": 1, "data": data}


def _order(quantity: Decimal = Decimal("0.01")) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        asset_class=AssetClass.CRYPTO,
    )


# ---------------------------------------------------------------------------
# negative >= 3
# ---------------------------------------------------------------------------


async def test_place_futures_order_rejects_zero_quantity() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_order(_order(quantity=Decimal("0")))


async def test_place_futures_order_rejects_negative_quantity() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_order(_order(quantity=Decimal("-0.01")))


async def test_get_ohlcv_rejects_unsupported_timeframe() -> None:
    """market_data_mixin.py::get_ohlcv — `_GRANULARITY_MAP`에 없는
    timeframe은 요청이 나가기 전에 거부돼야 한다(get_history_candles와
    동일 계약, KIS test_get_ohlcv_rejects_unsupported_timeframe와 동일
    패턴인데 Bitget 쪽엔 지금까지 이 경계 테스트가 전혀 없었다)."""
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.get_ohlcv("BTC/USDT", "3m")


async def test_get_history_candles_rejects_unsupported_timeframe() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.get_history_candles("BTC/USDT", "2h")


async def test_get_futures_order_raises_keyerror_on_response_missing_order_id() -> None:
    """fail-closed 계약 — `_row_to_futures_order`가 `data["orderId"]`를
    직접 인덱싱하므로, 거래소가 성공 코드와 함께 계약을 어긴(orderId
    누락) 바디를 주면 조용히 빈 값으로 넘어가지 않고 KeyError로 즉시
    터져야 한다(잘못된 Order를 만들어 내는 것보다 낫다)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope("00000", [{"state": "live"}]))

    adapter = _make_adapter(handler)
    with pytest.raises(KeyError):
        await adapter.get_futures_order("order-1", symbol="BTC/USDT")


# ---------------------------------------------------------------------------
# failure-injection — place_futures_order가 인프라 장애를 흡수 후 성공하는지
# ---------------------------------------------------------------------------


async def test_place_futures_order_survives_infra_failures_then_succeeds() -> None:
    """failure-injection — 이 주문 경로는 지금까지 어떤 테스트에서도 실제
    전송계층 실패(`httpx.ConnectError`)를 주입받은 적이 없었다(기존
    negative는 전부 로컬 검증뿐). `ResilientTransport`가 2회의 연결 실패를
    흡수하고 세 번째 시도에서 성공해야 한다."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise httpx.ConnectError("simulated network failure", request=request)
        assert request.url.path == "/api/v2/mix/order/place-order"
        return httpx.Response(200, json=_envelope("00000", {"orderId": "f-1"}))

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_order(_order())

    assert calls == 3
    assert result.exchange_order_id == "f-1"
    assert result.status is OrderStatus.SUBMITTED


# ---------------------------------------------------------------------------
# 수치 성능 단언 — get_futures_open_orders() 대량 파싱 처리량
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_get_futures_open_orders_parses_large_list_within_normalized_budget() -> None:
    """수치 성능 단언 — `[_row_to_futures_order(row) for row in ...]`은
    항목 수에 선형으로 늘어야 한다. 절대 ms 상수 대신 같은 프로세스에서
    잰 동일 크기 baseline 대비 정규화 배율을 쓴다(task-2807
    test_account_mode_deepen.py와 동일 결정)."""
    n = 2000
    repeats = 5
    rows = [
        {"orderId": f"o-{i}", "state": "live", "symbol": "BTCUSDT", "side": "buy"} for i in range(n)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope("00000", {"entrustedList": rows}))

    adapter = _make_adapter(handler)

    async def _call_once() -> None:
        result = await adapter.get_futures_open_orders()
        assert len(result) == n

    await _call_once()  # 워밍업

    measured_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        await _call_once()
        measured_times.append(time.perf_counter() - start)
    measured_seconds = min(measured_times)

    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        baseline = [dict(row) for row in rows]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = measured_seconds / baseline_seconds
    budget_ratio = 30000.0
    print(
        f"\nget_futures_open_orders throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms measured={measured_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_futures_open_orders() 대량 파싱이 baseline dict 복사 대비 "
        f"{ratio:.1f}배로 회귀했습니다(예산 {budget_ratio}배) — 이차 이상 복잡도 유입 가능성."
    )


# ---------------------------------------------------------------------------
# 게이트 적색 재현 — @require_paper_sandbox AST 스캐너가 이 세 파일(futures_
# trading_mixin/trading_mixin, adapter.py는 이들을 조립만 함)의 실제 자금
# 이동 메서드 이름을 정확히 잡아내는지
# ---------------------------------------------------------------------------

_SPLIT_MIXIN_FUND_MOVING_METHODS = (
    "place_order",
    "cancel_order",
    "modify_order",
    "place_batch_orders",
    "cancel_batch_orders",
    "place_futures_order",
    "modify_futures_order",
    "cancel_futures_order",
    "close_futures_position",
    "cancel_all_futures_orders",
)


def test_gate_flags_split_mixin_methods_if_guard_decorator_is_dropped() -> None:
    """게이트 적색 재현 — trading_mixin.py/futures_trading_mixin.py의 실제
    자금이동 메서드 이름들로 만든 합성 소스에서 데코레이터를 제거하면
    `test_live_guard_coverage.py`의 AST 스캐너가 전부 위반으로 잡아내는지
    확인한다. task-1032 분할 이후 이 정확한 이름 집합에 대한 회귀 방어가
    없었다."""
    src = "class X:\n" + "".join(
        f"    async def {name}(self, *a, **kw):\n        return True\n\n"
        for name in _SPLIT_MIXIN_FUND_MOVING_METHODS
    )
    tree = ast.parse(src)
    async_defs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert {n.name for n in async_defs} == set(_SPLIT_MIXIN_FUND_MOVING_METHODS)

    for node in async_defs:
        assert not _is_stub_body(node.body)
        assert not _has_guard_decorator(node.decorator_list), (
            f"{node.name}은(는) 데코레이터가 없는 합성 소스인데도 스캐너가 "
            "'가드 있음'으로 오판했습니다 — 스캐너 자체가 고장난 회귀."
        )


def test_gate_recognizes_guard_decorator_present_on_split_mixin_methods() -> None:
    """대칭 검증 — 실제 데코레이터가 붙은 합성 소스는 스캐너가 위반으로
    오탐하지 않아야 한다."""
    src = "class X:\n" + "".join(
        f"    @require_paper_sandbox\n    async def {name}(self, *a, **kw):\n        "
        "return True\n\n"
        for name in _SPLIT_MIXIN_FUND_MOVING_METHODS
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            assert _has_guard_decorator(node.decorator_list)


def test_bitget_adapter_actually_declares_all_split_mixin_bases() -> None:
    """게이트 적색 재현 보강 — task-1032가 분할한 믹스인 중 하나라도
    `BitgetAdapter`의 MRO에서 빠지면(예: import 누락) 위 두 스캐너 테스트는
    소스 전체 스캔이 아니라 합성 소스만 보므로 못 잡는다. 실제 어댑터가
    각 파일의 클래스를 전부 상속하는지 별도로 고정한다."""
    from src.exchanges.bitget.futures_trading_mixin import BitgetFuturesTradingMixin
    from src.exchanges.bitget.market_data_mixin import BitgetMarketDataMixin
    from src.exchanges.bitget.trading_mixin import BitgetTradingMixin

    assert issubclass(BitgetAdapter, BitgetFuturesTradingMixin)
    assert issubclass(BitgetAdapter, BitgetMarketDataMixin)
    assert issubclass(BitgetAdapter, BitgetTradingMixin)
