"""task-2780 DEEPEN of task-1786 (BR-8, ADR-2026-09-06-I D2, ADR-H D7).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1786)는 원 커밋(7d2b7ef2)의
4개 negative 테스트(게이트-우회 콜 카운트 확인 포함)가 D2 하한(failure-
injection·수치 성능/지연 단언·이전에 적색이었던 CI 게이트 시나리오 재현)에
못 미친다고 판정했다(실측 D1). `order_dispatch.py`/`adapter.py`는 손대지
않고, `test_kis_capability_matrix.py`가 다루지 않은 세 각도만 보강한다.

1) failure-injection: `place_order()`(adapter.py:169, `dispatch_place_order`
   진입점)를 통해 국내/해외 선물옵션 분기까지 실제로 실패를 주입한다 —
   기존 test_kis_capability_matrix.py는 성공 왕복(routing)과 CRYPTO
   즉시거부만 증명했지, 국내/해외 선물옵션 분기가 네트워크 유실·비JSON
   응답·비즈니스 거부·토큰 발급 실패 아래서 fail-closed로 전파되는지는
   증명하지 않았다(domestic_futureoption_mixin.py/overseas_futureoption_mixin.py
   자체 단위테스트에도 이런 실패주입이 없다 — grep 확인, 2026-09-10).
2) 수치 성능/지연 단언: 절대 ms 상수 대신, 같은 프로세스 안에서 즉시
   측정한 원시 `_request` 왕복 총소요시간에 정규화한 배율 임계를 쓴다
   (task-2765/test_submit_order_failure_injection.py, task-2772/
   test_kis_overseas_deepen.py 선례와 동일 판단).
3) 게이트/CI 적색선 회귀: `order_dispatch.dispatch_place_order`의 마지막
   fail-closed 가드(미선언 자산군 즉시 거부 `raise FatalExchangeError`)를
   자식 pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
   `test_kis_capability_matrix.py::
   test_place_order_rejects_undeclared_asset_class_without_any_http_call`이
   green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_paper_drop_injection.py/task-1605 선례).
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter

_TOKEN_PATH = "/oauth2/tokenP"
_DOMESTIC_FO_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_OVERSEAS_FO_ORDER_PATH = "/uapi/overseas-futureoption/v1/trading/order"
_DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"

_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": ""}


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy`의 백오프 대기를 건너뛴다 — 실패주입 테스트가 재시도
    4회를 실제 시간만큼 기다리지 않게 한다(오직 이 목적, test_kis_overseas_
    deepen.py와 동일 기법)."""


def _make_paper_adapter(handler) -> KISAdapter:
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
        sleep_fn=_instant_sleep,
    )


def _order(asset_class: AssetClass, symbol: str) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=asset_class,
    )


# ---------------------------------------------------------------------------
# 1) failure-injection — place_order() 진입점을 통한 선물옵션 분기
# ---------------------------------------------------------------------------


async def test_place_order_domestic_futures_network_drop_exhausts_retries() -> None:
    """`place_order()`(top-level ABC 진입점)가 KR_FUTURES를 국내선물옵션
    엔드포인트로 분기한 뒤 네트워크가 계속 끊기면, `RetryPolicy.max_attempts`
    (4회)만큼 재시도하고서도 실패하면 삼키지 않고 `RetryableExchangeError`로
    전파한다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.KR_FUTURES, "101W09")

    with pytest.raises(RetryableExchangeError):
        await adapter.place_order(order)

    assert call_count["n"] == 4  # RetryPolicy 기본 max_attempts
    assert order.status == OrderStatus.CREATED  # 원본 Order는 그대로(불변)


async def test_place_order_domestic_option_malformed_json_never_produces_submitted() -> None:
    """국내옵션 주문 응답이 JSON이 아니면(HTML 오류 페이지 등) 바디 레벨
    실패는 재시도 없이 단발로 `RetryableExchangeError`를 올린다 — 예외가
    나면 `place_futureoption_order`는 `model_copy`에 도달하지 못하므로
    SUBMITTED로 표시된 Order가 새로 만들어질 수 없다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.KR_OPTION, "201W09")

    with pytest.raises(RetryableExchangeError):
        await adapter.place_order(order)

    assert business_calls["n"] == 1  # 바디 레벨 실패는 재시도 없이 단발
    assert order.status == OrderStatus.CREATED


async def test_place_order_overseas_derivative_business_rejection_never_produces_submitted() -> (
    None
):
    """해외선물옵션 주문이 KIS 쪽 비즈니스 사유로 거부되면(rt_cd != "0")
    조용히 실패로 전환되는 대신 `RetryableExchangeError`가 나고, 원본
    Order는 여전히 CREATED로 남는다(부분 SUBMITTED 상태가 만들어지지
    않음 — DoD 핵심 불변식)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _OVERSEAS_FO_ORDER_PATH:
            return httpx.Response(200, json={"rt_cd": "1", "msg1": "REJECTED"})
        raise AssertionError(f"예상치 못한 경로: {request.url.path}")

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.OVERSEAS_FUTURES, "ESZ26")

    with pytest.raises(RetryableExchangeError):
        await adapter.place_order(order)

    assert order.status == OrderStatus.CREATED


async def test_place_order_domestic_derivative_token_fetch_failure_raises_fatal() -> None:
    """토큰 발급 자체가 실패하면(500) `place_order()`가 선물옵션으로
    분기하는 경로에서도 `_fetch_token`의 변환 규칙대로 `FatalExchangeError`가
    나고, 주문 엔드포인트에는 어떤 요청도 나가지 않는다 — BR-8이 새로
    연 선물옵션 분기가 기존 도메스틱주식 경로와 동일한 토큰 실패 방어를
    상속함을 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(500, json={"error": "server error"})
        raise AssertionError("토큰 발급 실패 시 주문 요청이 나가면 안 됩니다")

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.KR_FUTURES, "101W09")

    with pytest.raises(FatalExchangeError):
        await adapter.place_order(order)


# ---------------------------------------------------------------------------
# 2) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _fast_futures_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        if request.url.path == _DOMESTIC_FO_ORDER_PATH:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            raise AssertionError(f"예상치 못한 경로: {request.url.path}")
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    return handler


async def test_place_order_domestic_futures_dispatch_overhead_bounded_vs_raw_request_baseline() -> (
    None
):
    """`place_order()`(top-level ABC 진입점, `require_paper_sandbox`
    이중 데코레이션 포함) → `dispatch_place_order` → `place_futureoption_order`
    체인이 원시 `_request` 왕복 하나만 하는 것과 같은 수의 HTTP 왕복(1회)을
    쓰므로, 총소요시간 배율은 CI 편차를 감안해도 작아야 한다. 절대 ms
    상수 대신 이 프로세스가 방금 측정한 원시 왕복 총소요시간에 정규화한
    배율을 임계로 쓴다(task-2772/test_kis_overseas_deepen.py 선례와
    동일 판단)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(_fast_futures_handler(captured))
    n = 100
    order = _order(AssetClass.KR_FUTURES, "101W09")

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter.place_order(order)
    captured.clear()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request(
            "POST", _DOMESTIC_FO_ORDER_PATH, "TTTO1101U", body={"noop": "1"}
        )
    baseline_elapsed = time.perf_counter() - baseline_started

    dispatch_started = time.perf_counter()
    for _ in range(n):
        await adapter.place_order(order)
    dispatch_elapsed = time.perf_counter() - dispatch_started

    ratio = dispatch_elapsed / baseline_elapsed
    budget_ratio = 6.0  # 둘 다 HTTP 왕복 1회씩이라 이론상 ~1배, 여유 6배
    print(
        f"\ndomestic futures place_order overhead: n={n} "
        f"baseline={baseline_elapsed * 1000:.1f}ms dispatch={dispatch_elapsed * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"국내선물옵션 place_order() 분기(order_dispatch.py) 오버헤드가 원시 요청 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — asset_class 분기/이중 "
        "require_paper_sandbox 데코레이션에 의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 — dispatch_place_order의 fail-closed 가드 제거
# ---------------------------------------------------------------------------

_GUARD = (
    "    raise FatalExchangeError(\n"
    '        f"KISAdapter.place_order: capabilities에 없는 자산군"\n'
    '        f"({order.asset_class.value})입니다 — get_capabilities()보다 먼저 "\n'
    '        "Validator가 걸러야 하는 주문이 여기 도달했습니다(상위 계층 버그, "\n'
    '        "fail-closed 방어)."\n'
    "    )\n"
)
_MUTATED = "    return order\n"


def test_dispatch_fail_closed_guard_source_matches_expected_snippet() -> None:
    """뮤테이션 테스트가 문자열 치환에 의존하므로, 프로덕션 소스가 예상한
    형태 그대로인지 먼저 확인한다(소스가 바뀌면 아래 subprocess 테스트가
    무의미하게 항상 통과하는 것을 방지 — count==1 어서션과 동일 목적을
    별도 단언으로 드러낸다)."""
    module = importlib.import_module("src.exchanges.kis.order_dispatch")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_GUARD) == 1


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.order_dispatch")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_dispatch_fail_closed_guard_is_removed(
    tmp_path: Path,
) -> None:
    """`dispatch_place_order`의 마지막 fail-closed 가드(미선언 자산군 즉시
    거부)를 자식 pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
    `test_kis_capability_matrix.py::
    test_place_order_rejects_undeclared_asset_class_without_any_http_call`이
    green(1 passed)에서 red(1 failed)로 뒤집힌다 — 가드 없이는 CRYPTO
    주문이 예외 없이 원본 Order를 그대로 반환해(HTTP 호출은 여전히 없지만)
    `pytest.raises(FatalExchangeError)`가 실패한다. 이는 BR-8이 이전에
    실제로 적색이었던 시나리오(get_capabilities()만 넓히고 place_order
    분기를 빼먹어 미선언 자산군이 조용히 통과하던 결함, 1bb228b3 커밋
    docstring 참조)를 재현한다."""
    target_test = (
        "tests/integration/exchanges/test_kis_capability_matrix.py::"
        "test_place_order_rejects_undeclared_asset_class_without_any_http_call"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_dispatch_fail_closed_guard"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
