"""task-3166 DEEPEN of task-1011 (PLT-40b, `docs/specs/L4_platform_
observability_tenancy_api_v1.0.md#§9 PLT-40`).

task-1011(2f12f264)은 `market_data_mixin.py`/`trading_mixin.py`/
`account_mixin.py`/`overseas_stock_mixin.py`의 `self._request` 등 접근에
붙어 있던 `# type: ignore[attr-defined]` 41건을 파일-로컬 확장 Protocol
(`_IntradayCandleClient`/`_BalanceCheckingClient`/`_OrderMutatingClient`,
bitget task-1001과 동일 패턴)로 제거한 순수 타입 리팩터였다 — 런타임 동작은
한 줄도 바꾸지 않았다고 커밋 메시지가 명시한다. 하지만 "순수 타입 리팩터"를
근거로 이 네 파일(+ ratchet 스크립트)을 직접 겨냥한 D2 증빙이 전무했다.

`overseas_stock_mixin.py`는 task-2776(test_overseas_stock_mixin_deepen.py)이,
`check_type_ignore_budget.py`는 task-2683(test_check_type_ignore_budget_
deepen.py)이 이미 D2를 채웠으므로 이 파일은 나머지 세 파일 — 특히 Protocol이
실제로 잇는 교차 믹스인 호출 지점(`get_ohlcv`→`_get_intraday_candles`,
`health_check`→`get_balance`, `modify_order`→`_rvsecncl`/`get_order`) —
에 집중한다. 새 기능 추가나 동작 변경 없음.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Generator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis import rate_profile
from src.exchanges.kis.account_mixin import KISAccountMixin
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.market_data_mixin import KISMarketDataMixin
from src.exchanges.kis.overseas_stock_mixin import KISOverseasStockMixin
from src.exchanges.kis.trading_mixin import KISTradingMixin


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Generator[None, None, None]:
    """`rate_profile.py`'s (account_type, tr_group) `TokenBucket` is a
    process-wide singleton (BR-2b) — whichever test creates it first locks in
    its `sleep` callable for every later test sharing the key. Reset before/
    after each test so this file's adapters always get a freshly built
    bucket wired to their own injected fake sleep, instead of possibly
    inheriting a real-`asyncio.sleep` bucket from test order."""
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy` 백오프를 건너뛴다 — 실패주입 테스트가 재시도 소진을
    실제 시간만큼 기다리지 않게 한다(오직 이 목적)."""


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep_fn: Callable[[float], Any] | None = None,
) -> KISAdapter:
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
        sleep_fn=sleep_fn,
    )


def _route(
    request: httpx.Request,
    routes: Mapping[str, Callable[[httpx.Request], httpx.Response]],
) -> httpx.Response:
    if request.url.path == _TOKEN_PATH:
        return httpx.Response(200, json=_TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


def _order(quantity: Decimal = Decimal("10")) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        asset_class=AssetClass.KR_EQUITY,
    )


# ---------------------------------------------------------------------------
# negative(>=3) — market_data_mixin.py/trading_mixin.py의 fail-closed 경로
# 중 지금까지 어떤 테스트도 직접 겨냥하지 않았던 지점
# ---------------------------------------------------------------------------


async def test_get_ticker_missing_expected_field_raises_fatal_exchange_error() -> None:
    """`get_ticker`는 두 엔드포인트 응답을 조합하는데, 지금까지 성공
    경로(test_get_ticker_combines_price_and_orderbook_endpoints)만
    있었고 응답 스키마가 깨진 경우(가격 필드 누락)는 한 번도 검증된
    적이 없었다."""

    def price_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    def book_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output1": {"askp1": "70100", "bidp1": "69900"}},
        )

    routes = {
        "/uapi/domestic-stock/v1/quotations/inquire-price": price_handler,
        "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn": book_handler,
    }
    adapter = _make_adapter(lambda request: _route(request, routes))

    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("005930")


async def test_get_orderbook_missing_output1_raises_fatal_exchange_error() -> None:
    """`get_orderbook`은 지금까지 이 저장소의 어떤 테스트에서도 호출된
    적이 없다(get_ticker 경유로만 같은 엔드포인트를 간접적으로 쳤다) —
    독립 호출 경로 자체와 그 fail-closed 가드를 함께 고정한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok"})  # output1 없음

    adapter = _make_adapter(
        lambda request: _route(
            request,
            {"/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn": handler},
        )
    )

    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_ohlcv_1d_missing_expected_field_raises_fatal_exchange_error() -> None:
    """일봉 조회 행에서 예상 필드(`stck_oprc`)가 없으면 조용히 0으로
    채우지 않고 즉시 FatalExchangeError로 터져야 한다 — 지금까지 1d
    경로의 negative test가 전혀 없었다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": [{"stck_bsop_date": "20260101"}]},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": handler}
        )
    )

    with pytest.raises(FatalExchangeError):
        await adapter.get_ohlcv("005930", "1d")


async def test_get_ohlcv_1m_missing_expected_field_raises_fatal_exchange_error() -> None:
    """PLT-40b가 `_IntradayCandleClient` Protocol로 계약에 편입한 교차
    믹스인 호출(`get_ohlcv`→`_get_intraday_candles`)이 실제로 배선돼
    있고, 그 안의 fail-closed 가드도 살아 있는지 함께 확인한다 —
    지금까지 1m 경로는 성공 케이스만 있었다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": [{"stck_bsop_date": "20260902"}]},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request,
            {"/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice": handler},
        )
    )

    with pytest.raises(FatalExchangeError):
        await adapter.get_ohlcv("005930", "1m")


async def test_get_order_raises_fatal_when_no_matching_rows() -> None:
    """`get_order`(trading_mixin.py)가 주문 조회 응답에 일치하는 행이
    없을 때 빈 Order를 만들어 돌려주지 않고 명시적으로 거부하는지 —
    `_OrderMutatingClient` Protocol이 `modify_order`에 노출하는 바로 그
    메서드인데 지금까지 직접 겨냥한 테스트가 없었다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output1": []})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-daily-ccld": handler}
        )
    )

    with pytest.raises(FatalExchangeError, match="KIS 주문을 찾을 수 없음"):
        await adapter.get_order("1234:999")


# ---------------------------------------------------------------------------
# failure-injection — Protocol이 잇는 교차 믹스인 호출 경계에서 실패가
# 삼켜지거나 위장되지 않는지
# ---------------------------------------------------------------------------


async def test_health_check_swallows_infra_failure_from_get_balance_after_retries() -> None:
    """`_BalanceCheckingClient` Protocol이 `health_check`(trading_mixin.py)
    에 노출하는 `get_balance`(account_mixin.py)가 네트워크 유실로 재시도
    예산(4회)을 전부 소진해 `RetryableExchangeError`를 던져도,
    `health_check`은 그 예외를 삼키고 False만 반환해야 한다(Watchdog이
    health_check 호출 자체로 죽으면 안 된다는 계약) — 지금까지 성공
    경로만 있었다."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        calls += 1
        raise httpx.ConnectError("simulated network failure", request=request)

    adapter = _make_adapter(handler, sleep_fn=_instant_sleep)

    assert await adapter.health_check() is False
    assert calls == 4  # RetryPolicy 기본 max_attempts, 전부 소진 후 삼켜짐


async def test_modify_order_propagates_get_order_failure_after_successful_rvsecncl() -> None:
    """`_OrderMutatingClient` Protocol이 `modify_order`에 잇는 두 번째
    교차 믹스인 호출(`get_order`)이 실패하면, 정정 요청(`_rvsecncl`) 자체는
    거래소에 이미 성공적으로 접수됐더라도 `modify_order`가 그 실패를
    삼키고 조작된(placeholder) Order를 위장해 돌려주면 안 된다 — 이
    두 단계 흐름은 지금까지 어떤 테스트에서도 왕복된 적이 없었다."""

    def rvsecncl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    def get_order_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output1": []})

    routes = {
        "/uapi/domestic-stock/v1/trading/order-rvsecncl": rvsecncl_handler,
        "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": get_order_handler,
    }
    adapter = _make_adapter(lambda request: _route(request, routes))

    with pytest.raises(FatalExchangeError, match="KIS 주문을 찾을 수 없음"):
        await adapter.modify_order("1234:999", quantity=Decimal("5"))


# ---------------------------------------------------------------------------
# 수치 성능 단언 — get_ohlcv(1d) 대량 파싱이 baseline 대비 정규화 배율 안인지
# ---------------------------------------------------------------------------


async def test_get_ohlcv_1d_parses_large_response_within_normalized_budget() -> None:
    """market_data_mixin.py::get_ohlcv(1d 경로)는 항목 수에 선형으로
    늘어야 한다. 절대 ms 상수 대신 같은 프로세스에서 잰 동일 크기
    baseline(dict 얕은 복사) 대비 정규화 배율을 쓴다(task-2807/
    bitget test_task1032_split_mixins_deepen.py와 동일 판단 — 공유 CI
    환경에서 절대 임계는 상시 적색을 낳는다)."""
    n = 2000
    repeats = 5
    rows = [
        {
            "stck_bsop_date": "20260101",
            "stck_oprc": "70000",
            "stck_hgpr": "70500",
            "stck_lwpr": "69900",
            "stck_clpr": "70200",
            "acml_vol": "1000",
        }
        for _ in range(n)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": rows})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": handler}
        )
    )

    async def _call_once() -> None:
        result = await adapter.get_ohlcv("005930", "1d", limit=n)
        assert len(result) == n

    await _call_once()  # 워밍업(토큰 발급 지연 제외)

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
        f"\nget_ohlcv(1d) throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms measured={measured_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_ohlcv(1d)(market_data_mixin.py) 대량 파싱이 baseline dict 복사 대비 "
        f"{ratio:.1f}배로 회귀했습니다(예산 {budget_ratio}배) — 이차 이상 복잡도 유입 가능성."
    )


# ---------------------------------------------------------------------------
# 게이트 적색 재현 — get_order()의 "주문 없음" fail-closed 가드 제거
# ---------------------------------------------------------------------------

_GUARD = (
    '        rows = raw.get("output1", [])\n'
    "        if not rows:\n"
    '            raise FatalExchangeError(f"KIS 주문을 찾을 수 없음: order_id={order_id}")\n'
    "        row = rows[0]\n"
)
_MUTATED = '        rows = raw.get("output1", []) or [{}]\n        row = rows[0]\n'


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.trading_mixin")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_get_order_not_found_guard_is_removed(
    tmp_path: Path
) -> None:
    """`get_order`의 "주문 없음" fail-closed 가드(`_OrderMutatingClient`
    Protocol이 `modify_order`에 노출하는 바로 그 메서드)를 자식 pytest
    프로세스 안에서만 구세대 `or [{}]` 관례로 되돌리면(프로덕션 소스는
    그대로), 이 파일의
    `test_get_order_raises_fatal_when_no_matching_rows`가 green(1 passed)
    에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
    test_kis_domestic_stock_extra_deepen.py/test_overseas_stock_mixin_
    deepen.py 선례) — 가드 없이는 존재하지 않는 주문 조회가 예외 없이
    빈 placeholder Order를 조용히 돌려준다."""
    target_test = (
        "tests/unit/exchanges/kis/test_task1011_protocol_mixins_deepen.py::"
        "test_get_order_raises_fatal_when_no_matching_rows"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_get_order_not_found_guard"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=mutated_env,
        timeout=120,
        check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


# ---------------------------------------------------------------------------
# 게이트 적색 재현(보강) — 믹스인 하나라도 MRO에서 빠지면(예: import 누락)
# Protocol은 구조적 타이핑이라 mypy는 여전히 통과하지만 런타임 AttributeError로
# 즉시 터진다. bitget test_task1032_split_mixins_deepen.py와 대칭.
# ---------------------------------------------------------------------------


def test_kis_adapter_actually_declares_all_task1011_mixin_bases() -> None:
    assert issubclass(KISAdapter, KISMarketDataMixin)
    assert issubclass(KISAdapter, KISTradingMixin)
    assert issubclass(KISAdapter, KISAccountMixin)
    assert issubclass(KISAdapter, KISOverseasStockMixin)
