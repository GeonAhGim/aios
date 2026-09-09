"""task-2776 DEEPEN of task-1782 (BR-4, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1782)는 원 커밋(88c84cb,
`overseas_stock_mixin.py`)이 >3 negative test는 갖췄지만 D2 하한(failure-
injection, 수치 성능/지연 단언, gate-red 회귀 테스트)에 못 미친다고
판정했다(실측 D1). 이 파일이 그 세 가지만 보강한다 — `overseas_stock_
mixin.py`는 손대지 않는다. `test_overseas_stock_mixin.py`(왕복/negative
테스트 본체)와 `tests/integration/exchanges/test_kis_overseas_deepen.py`
(task-2772, order_dispatch.py 스콥의 failure-injection)와는 각도를
겹치지 않게 잡았다:

1) failure-injection: 이 파일은 `place_overseas_order`/`cancel_overseas_
   order`/`get_overseas_balance`에 각각 다른 실패 형태(비즈니스 거부,
   비JSON 응답, 네트워크 유실)를 주입해 예외가 삼켜지지 않고 fail-closed로
   전파됨을 증명한다(task-2772가 다룬 get_overseas_ticker 네트워크 유실/
   place_overseas_order 비JSON/cancel 비즈니스 거부/토큰 발급 실패와는
   각각 다른 조합).
2) 수치 성능/지연 단언: 절대 ms 상수 대신, 같은 프로세스 안에서 즉시
   측정한 원시 `_request` 왕복 총소요시간에 정규화한 배율 임계를 쓴다
   (task-2765/test_submit_order_failure_injection.py, task-2772 선례와
   동일 판단). task-2772는 `dispatch_place_order`(order_dispatch.py)의
   오버헤드를 쟀고, 이 파일은 `place_overseas_order` 자체(이 리프의
   대상 파일)의 오버헤드를 잰다.
3) 게이트/CI 적색선 회귀: `_exchange_codes`의 미지원 거래소 거부 가드
   (fail-closed, BR-4의 핵심)를 자식 pytest 프로세스 안에서만 제거하면
   `test_overseas_stock_mixin.py::test_unsupported_exchange_is_rejected_
   explicitly`가 green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다
   (동일 기법, test_kis_overseas_deepen.py/test_paper_drop_injection.py
   선례).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter

_TOKEN_PATH = "/oauth2/tokenP"
_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"

_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": ""}


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy`의 백오프 대기를 건너뛴다 — 실패주입 테스트가 재시도
    만큼 실제 시간을 기다리지 않게 한다(오직 이 목적)."""


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


def _order(symbol: str = "AAPL") -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.US_EQUITY,
    )


# ---------------------------------------------------------------------------
# 1) failure-injection
# ---------------------------------------------------------------------------


async def test_place_overseas_order_business_rejection_raises_and_order_stays_created() -> None:
    """미국 외 거래소(SEHK)에서 주문이 거래소에 의해 비즈니스 거부(rt_cd
    != "0")되면 `_request`가 이를 예외로 승격시켜 `place_overseas_order`는
    `model_copy`에 도달하지 못한다 — 원본 Order는 CREATED 그대로 남고
    SUBMITTED로 위장된 새 Order가 만들어지지 않는다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, json={"rt_cd": "1", "msg1": "INSUFFICIENT_BALANCE"})

    adapter = _make_paper_adapter(handler)
    order = _order("0700")

    with pytest.raises(RetryableExchangeError):
        await adapter.place_overseas_order(order, "SEHK")

    assert business_calls["n"] == 1  # 바디 레벨 실패는 재시도 없이 단발
    assert order.status == OrderStatus.CREATED


async def test_cancel_overseas_order_malformed_json_raises_without_returning_bool() -> None:
    """취소 응답이 JSON이 아니면(게이트웨이 오류 페이지 등) `cancel_
    overseas_order`가 `False`를 반환해 "취소 실패"로 오인되게 두지 않고
    예외로 전파한다 — task-2772는 취소 경로의 비즈니스 거부(rt_cd="1")를
    다뤘고, 이 테스트는 같은 경로의 비JSON 응답을 다룬다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_overseas_order(
            "ORG:1", "AAPL", "NASD", original_quantity=Decimal("1")
        )

    # 바디 레벨 실패(HTTP 200 + 비JSON)는 단발, 불리언으로 삼켜지지 않음
    assert business_calls["n"] == 1


async def test_get_overseas_balance_network_drop_exhausts_retries_then_raises() -> None:
    """잔고조회 경로에서 네트워크가 계속 끊기면 `RetryPolicy.max_attempts`
    (4회)만큼 재시도하고서도 실패하면 삼키지 않고 `RetryableExchangeError`로
    전파한다 — task-2772가 다룬 시세조회 네트워크 유실과 달리 잔고조회
    경로를 확인한다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_overseas_balance("NASD")

    assert call_count["n"] == 4  # RetryPolicy 기본 max_attempts


# ---------------------------------------------------------------------------
# 2) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _fast_order_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    return handler


async def test_place_overseas_order_overhead_bounded_vs_raw_request_baseline() -> None:
    """`place_overseas_order`(이 리프의 대상 함수)는 `_exchange_codes`
    조회 + body 조립 + `_request` 왕복 1회만 하므로, 원시 `_request` 왕복
    하나만 하는 것과 총소요시간 배율이 크게 벌어지면 안 된다. 절대 ms
    상수 대신 이 프로세스가 방금 측정한 원시 왕복 총소요시간에 정규화한
    배율을 임계로 쓴다(task-2765/test_kis_overseas_deepen.py 선례와 동일
    판단 — 공유 CI 환경에서 절대 임계는 상시 적색을 낳는다)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(_fast_order_handler(captured))
    n = 100

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter._request(
        "POST", _ORDER_PATH, "TTTT1002U", body={"warmup": "1"}
    )
    captured.clear()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request("POST", _ORDER_PATH, "TTTT1002U", body={"k": "v"})
    baseline_elapsed = time.perf_counter() - baseline_started

    order = _order("AAPL")
    order_started = time.perf_counter()
    for _ in range(n):
        await adapter.place_overseas_order(order, "NASD")
    order_elapsed = time.perf_counter() - order_started

    ratio = order_elapsed / baseline_elapsed
    budget_ratio = 6.0  # 둘 다 HTTP 왕복 1회씩이라 이론상 ~1배, 여유 6배
    print(
        f"\nplace_overseas_order overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"order={order_elapsed * 1000:.1f}ms ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"place_overseas_order(overseas_stock_mixin.py) 오버헤드가 원시 요청 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — 거래소 코드 조회/body "
        "조립에 의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 — _exchange_codes 미지원 거래소 거부 가드 제거
# ---------------------------------------------------------------------------

_GUARD = (
    "    if codes is None:\n"
    "        raise ValueError(\n"
    "            f\"지원하지 않는 해외주식 거래소입니다: {exchange!r} \"\n"
    "            f\"(지원: {', '.join(_EXCHANGES)}) — 시세조회용 3자리 코드(EXCD, \"\n"
    "            \"예: NAS)가 아니라 주문용 4자리 코드(OVRS_EXCG_CD, 예: NASD)를 \"\n"
    "            \"써야 합니다.\"\n"
    "        )\n"
)
_MUTATED = ""


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.overseas_stock_mixin")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_unsupported_exchange_guard_is_removed(
    tmp_path: Path,
) -> None:
    """`_exchange_codes`의 미지원 거래소 거부 가드(fail-closed, BR-4의
    핵심)를 자식 pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
    `test_overseas_stock_mixin.py::test_unsupported_exchange_is_rejected_
    explicitly`가 green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다 —
    가드 없이는 미지원 거래소가 `ValueError` 없이 조용히 `None`을 반환한다."""
    target_test = (
        "tests/unit/exchanges/kis/test_overseas_stock_mixin.py::"
        "test_unsupported_exchange_is_rejected_explicitly"
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

    plugin_module_name = "_mutate_unsupported_exchange_guard"
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
