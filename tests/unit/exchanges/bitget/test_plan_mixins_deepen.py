"""task-3161 DEEPEN of task-1032 (PLT-40a 선행, commit 미상 — trading_mixin.py/
futures_trading_mixin.py를 P6 line_cap 준수를 위해 순수 이동만으로 분할).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-40

`trading_plan_mixin.py`(BitgetTradingPlanMixin, Spot Plan 주문군 + health_check)
와 `futures_plan_mixin.py`(BitgetFuturesPlanMixin, Futures Plan/TPSL 주문군)는
task-1032가 순수 이동으로 신설한 파일인데, 원 리프도 그 이후로도 이 두 파일을
직접 겨냥한 전용 테스트 파일이 없었다 — 해피패스는
`tests/integration/test_bitget_adapter.py`(spot plan)와
`tests/integration/test_bitget_futures.py`(futures plan)에 조립된 어댑터
경유로만 존재하고, D2 하한(negative>=3/실패주입 1/수치 성능단언 1/게이트
적색재현 1)에 필요한 증빙은 전무했다. 새 기능 추가나 동작 변경 없이 이
증빙만 채운다.
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
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from tests.unit.exchanges.test_live_guard_coverage import (
    _has_guard_decorator,
    _is_stub_body,
)


async def _no_delay_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response], *, demo_mode: bool = True
) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key",
        "secret",
        "passphrase",
        demo_mode=demo_mode,
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
# negative >= 3 — futures_plan_mixin.py의 입력 검증 경계값
# ---------------------------------------------------------------------------


async def test_place_futures_tpsl_order_rejects_zero_trigger_price() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_tpsl_order("BTC/USDT", "profit_plan", Decimal("0"))


async def test_place_futures_tpsl_order_rejects_negative_size() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_tpsl_order(
            "BTC/USDT", "profit_plan", Decimal("85000"), size=Decimal("-1")
        )


async def test_place_futures_plan_order_rejects_zero_quantity() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_plan_order(_order(quantity=Decimal("0")), Decimal("75000"))


async def test_place_futures_plan_order_rejects_negative_trigger_price() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_plan_order(_order(), Decimal("-1"))


async def test_place_futures_position_tpsl_rejects_zero_take_profit_even_though_provided() -> None:
    """경계값 — `take_profit_trigger=0`은 `is not None` 검사는 통과하지만
    (즉 "적어도 하나는 필요" 에러가 아니라) 0보다 커야 한다는 별도 검증에
    걸려야 한다. 두 체크를 혼동하면(예: `if not take_profit_trigger`) 0을
    None과 같이 취급해 잘못된 에러 메시지로 새는 회귀를 잡는다."""
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_position_tpsl("BTC/USDT", take_profit_trigger=Decimal("0"))


async def test_place_futures_position_tpsl_rejects_negative_stop_loss() -> None:
    adapter = _make_adapter(lambda request: pytest.fail("가드가 막았어야 할 요청"))
    with pytest.raises(ValueError):
        await adapter.place_futures_position_tpsl("BTC/USDT", stop_loss_trigger=Decimal("-100"))


# ---------------------------------------------------------------------------
# failure-injection — trading_plan_mixin.health_check()가 어떤 예외든 삼키는지
# ---------------------------------------------------------------------------


async def test_health_check_swallows_unexpected_exception_from_get_balance() -> None:
    """비즈니스 오류(빈 잔고 등)가 아니라 완전히 예상 밖의 예외(파싱 버그,
    None 역참조 등)가 get_balance()에서 올라와도 health_check()는 여전히
    False만 반환해야 한다 — Watchdog이 health_check() 호출 자체로 죽으면
    안 된다는 계약(docstring)의 실제 검증."""

    def handler(request: httpx.Request) -> httpx.Response:
        # 코드는 성공이지만 스키마가 깨진 응답 -> get_balance 파싱 중
        # 예상 못한 예외(KeyError/TypeError 계열)가 발생한다.
        return httpx.Response(200, json=_envelope("00000", "not-a-list"))

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is False


async def test_health_check_swallows_infra_failure_after_retries_exhausted() -> None:
    """failure-injection — 인프라 장애(`httpx.ConnectError`)가
    `ResilientTransport`의 재시도 예산(max_attempts=4)을 전부 소진해
    `RetryableExchangeError`로 표면화되어도 health_check()는 그 예외를
    삼키고 False를 반환해야 한다(전파돼 Watchdog 루프를 죽이면 안 됨)."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("simulated network failure", request=request)

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is False
    assert calls == 4  # 최초 1회 + 재시도 3회, 전부 소진 후 삼켜짐


# ---------------------------------------------------------------------------
# 수치 성능 단언 — get_futures_current_plan_orders() 대량 파싱 처리량
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_get_futures_current_plan_orders_parses_large_list_within_normalized_budget() -> None:
    """수치 성능 단언 — `list(raw["data"].get("entrustedList") or [])`는
    얕은 복사뿐이라 항목 수에 선형으로 늘어야 한다. 절대 ms 상수 대신 같은
    프로세스에서 잰 동일 크기 trivial list 생성 대비 정규화 배율을 쓴다
    (task-2807 test_account_mode_deepen.py와 동일 결정)."""
    n = 2000
    repeats = 5
    rows = [{"orderId": f"o-{i}", "triggerPrice": "75000"} for i in range(n)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope("00000", {"entrustedList": rows}))

    adapter = _make_adapter(handler)

    async def _call_once() -> None:
        result = await adapter.get_futures_current_plan_orders()
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
        baseline = list(rows)
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = measured_seconds / baseline_seconds
    budget_ratio = 30000.0
    print(
        f"\nget_futures_current_plan_orders throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms measured={measured_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_futures_current_plan_orders() 대량 파싱이 trivial list 생성 대비 "
        f"{ratio:.1f}배로 회귀했습니다(예산 {budget_ratio}배) — 이차 이상 복잡도 유입 가능성."
    )


# ---------------------------------------------------------------------------
# 게이트 적색 재현 — @require_paper_sandbox AST 스캐너가 이 두 파일의 실제
# 메서드 이름을 정확히 잡아내는지 (test_live_guard_coverage.py의 일반
# 예시 하나만으로는 이 파일들의 구체적 이름 집합을 보장하지 못한다)
# ---------------------------------------------------------------------------

_PLAN_MIXIN_FUND_MOVING_METHODS = (
    "place_plan_order",
    "cancel_plan_order",
    "place_futures_tpsl_order",
    "place_futures_position_tpsl",
    "place_futures_plan_order",
    "cancel_futures_plan_order",
)


def test_gate_flags_plan_mixin_methods_if_guard_decorator_is_dropped() -> None:
    """게이트 적색 재현 — trading_plan_mixin.py/futures_plan_mixin.py의
    실제 자금이동 메서드 이름들로 만든 합성 소스에서 데코레이터를 제거하면
    `test_live_guard_coverage.py`의 AST 스캐너(`_has_guard_decorator`)가
    전부 위반으로 잡아내는지 확인한다. 이 이름 집합 자체가 스캐너의
    `_FUND_MOVING_NAME` 정규식과 우연히 안 맞을 위험(예: 명명 드리프트)을
    이 두 파일 범위로 좁혀 직접 고정한다."""
    src = "class X:\n" + "".join(
        f"    async def {name}(self, *a, **kw):\n        return True\n\n"
        for name in _PLAN_MIXIN_FUND_MOVING_METHODS
    )
    tree = ast.parse(src)
    async_defs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert {n.name for n in async_defs} == set(_PLAN_MIXIN_FUND_MOVING_METHODS)

    for node in async_defs:
        assert not _is_stub_body(node.body)
        assert not _has_guard_decorator(node.decorator_list), (
            f"{node.name}은(는) 데코레이터가 없는 합성 소스인데도 스캐너가 "
            "'가드 있음'으로 오판했습니다 — 스캐너 자체가 고장난 회귀."
        )


def test_gate_recognizes_guard_decorator_present_on_plan_mixin_methods() -> None:
    """위 테스트의 반대 방향 — 실제 데코레이터가 붙은 합성 소스는 스캐너가
    위반으로 오탐하지 않아야 한다(거짓 양성 방지, 대칭 검증)."""
    src = "class X:\n" + "".join(
        f"    @require_paper_sandbox\n    async def {name}(self, *a, **kw):\n        "
        "return True\n\n"
        for name in _PLAN_MIXIN_FUND_MOVING_METHODS
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            assert _has_guard_decorator(node.decorator_list)
