"""task-2772 DEEPEN of task-1776 (L4-32, ADR-2026-09-06-H D7).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1776)는 원 커밋(280c9aae)이
6개 거래소 파라미터화 positive USER_SCOPED 테스트 + negative 1개(국내주식은
태깅 안 됨)만 갖췄고 D3 하한(failure-injection·수치 성능/지연 단언·게이트/CI
적색선 회귀·다중 인스턴스/리플레이 적대적 증명)에 못 미친다고 판정했다(실측
D1). 이 파일이 그 네 가지만 보강한다 — `overseas_stock_mixin.py`/
`order_dispatch.py`는 손대지 않는다.

1) failure-injection: 시세/주문/취소 각 경로에 네트워크 유실·비JSON
   응답·비즈니스 거부를 주입해 예외가 삼켜지지 않고 fail-closed로
   전파됨을 증명한다. 그중 하나는 부수적 발견을 사실 그대로 남긴다 —
   `cancel_overseas_order`의 `return bool(raw.get("rt_cd") == "0")`는
   `_request`가 rt_cd != "0" 응답을 이미 예외로 승격시키므로(oauth_client.py
   `_classify_body`) 절대 `False`를 반환할 수 없는 죽은 분기다: 도달하면
   이미 rt_cd == "0"이었다는 뜻이라 항상 `True`이거나, 그 전에 예외가 난다.
   이 리프의 스콥(테스트 전용)에서는 고치지 않고 사실로만 남긴다.
2) 수치 성능/지연 단언: 절대 ms 상수 대신, 같은 프로세스 안에서 즉시
   측정한 원시 `_request` 왕복 총소요시간에 정규화한 배율 임계를 쓴다
   (task-2765/test_submit_order_failure_injection.py 선례와 동일 판단 —
   공유 CI 환경에서 절대 임계는 상시 적색을 낳는다).
3) 게이트/CI 적색선 회귀: `order_dispatch._split_overseas_symbol`의
   콜론 형식 가드(fail-closed, D7의 핵심)를 자식 pytest 프로세스 안에서만
   제거하면 `test_kis_capability_matrix.py::
   test_place_order_rejects_overseas_symbol_without_exchange_prefix`가
   green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_paper_drop_injection.py/task-1605 선례).
4) 다중 인스턴스/리플레이: 어댑터 인스턴스 하나를 여러 거래소로 동시에
   공유해도(토큰 캐시/락 공유) 거래소 코드가 서로 섞이지 않음, 토큰 발급이
   여전히 1회로 단일화됨, 그리고 동일 Order 객체를 재전송하면(어댑터
   docstring이 명시하듯 client_order_id를 KIS에 전달하지 않으므로) 중복
   방지 없이 그대로 두 번 나간다는 사실을 증명한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import (
    FatalExchangeError,
    FrozenZonePaperAdapterBlockedError,
    RetryableExchangeError,
)
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.order_dispatch import dispatch_place_order

_TOKEN_PATH = "/oauth2/tokenP"
_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"

_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": ""}


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy`의 백오프 대기를 건너뛴다 — 실패주입 테스트가 재시도
    4회를 실제 시간만큼 기다리지 않게 한다(오직 이 목적)."""


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
# 1) failure-injection
# ---------------------------------------------------------------------------


async def test_quote_network_drop_exhausts_retries_then_raises_retryable() -> None:
    """시세조회 경로에서 네트워크가 계속 끊기면 `RetryPolicy.max_attempts`
    (4회)만큼 재시도하고서도 실패하면 삼키지 않고 `RetryableExchangeError`로
    전파한다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_overseas_ticker("AAPL", "NASD")

    assert call_count["n"] == 4  # RetryPolicy 기본 max_attempts


async def test_place_order_malformed_json_raises_without_producing_submitted_order() -> None:
    """주문 응답이 JSON이 아니면(HTML 오류 페이지 등) `_classify_body`가
    단발 평가로 예외를 올린다(`ResilientTransport`가 바디 레벨 실패는
    재시도하지 않음) — 예외가 나면 `place_overseas_order`는 절대
    `model_copy`에 도달하지 못하므로 SUBMITTED로 표시된 Order가 새로
    만들어질 수 없다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.US_EQUITY, "AAPL")

    with pytest.raises(RetryableExchangeError):
        await adapter.place_overseas_order(order, "NASD")

    assert business_calls["n"] == 1  # 바디 레벨 실패는 재시도 없이 단발
    assert order.status == OrderStatus.CREATED  # 원본 Order는 그대로(불변)


async def test_cancel_order_business_rejection_raises_and_never_returns_false() -> None:
    """부수 발견 — `cancel_overseas_order`의 `return bool(raw.get("rt_cd")
    == "0")`는 죽은 분기다: `_request`가 rt_cd != "0" 응답을 이미 예외로
    승격시키므로, 이 함수에 도달한 시점엔 항상 rt_cd == "0"이다. 비즈니스
    거부(rt_cd="1")를 주입하면 `False`가 아니라 예외가 난다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _CANCEL_PATH:
            return httpx.Response(200, json={"rt_cd": "1", "msg1": "REJECTED"})
        raise AssertionError(f"예상치 못한 경로: {request.url.path}")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_overseas_order(
            "ORG:1", "AAPL", "NASD", original_quantity=Decimal("1")
        )


async def test_token_fetch_failure_during_overseas_quote_raises_fatal() -> None:
    """토큰 발급 자체가 실패하면(500) 시세조회 경로에서도 `_fetch_token`의
    변환 규칙대로 `FatalExchangeError`가 난다 — 국내주식 경로 전용이 아니라
    해외주식 진입점에서도 동일하게 방어됨을 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(500, json={"error": "server error"})
        raise AssertionError("토큰 발급 실패 시 시세조회 요청이 나가면 안 됩니다")

    adapter = _make_paper_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_overseas_ticker("AAPL", "NASD")


# ---------------------------------------------------------------------------
# 2) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _fast_success_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        if request.url.path == _QUOTE_PATH:
            output: dict[str, str] = {"last": "10.5", "tvol": "1"}
        elif request.url.path == _ORDER_PATH:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            raise AssertionError(f"예상치 못한 경로: {request.url.path}")
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    return handler


async def test_overseas_dispatch_routing_overhead_bounded_vs_raw_request_baseline() -> None:
    """`dispatch_place_order`의 US_EQUITY 분기(symbol 파싱 + 거래소 코드
    조회 + place_overseas_order)가 원시 `_request` 왕복 하나만 하는 것과
    같은 수의 HTTP 왕복(1회)을 쓰므로, 총소요시간 배율은 CI 편차를 감안해도
    작아야 한다. 절대 ms 상수 대신 이 프로세스가 방금 측정한 원시 왕복
    총소요시간에 정규화한 배율을 임계로 쓴다(task-2765 선례와 동일 판단)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(_fast_success_handler(captured))
    n = 100

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter._request(
        "GET", _QUOTE_PATH, "HHDFS00000300", params={"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}
    )
    captured.clear()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request(
            "GET", _QUOTE_PATH, "HHDFS00000300", params={"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}
        )
    baseline_elapsed = time.perf_counter() - baseline_started

    order = _order(AssetClass.US_EQUITY, "NASD:AAPL")
    dispatch_started = time.perf_counter()
    for _ in range(n):
        await dispatch_place_order(adapter, order)
    dispatch_elapsed = time.perf_counter() - dispatch_started

    ratio = dispatch_elapsed / baseline_elapsed
    budget_ratio = 6.0  # 둘 다 HTTP 왕복 1회씩이라 이론상 ~1배, 여유 6배
    print(
        f"\noverseas dispatch overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"dispatch={dispatch_elapsed * 1000:.1f}ms ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"해외주식 주문 분기(order_dispatch.py) 오버헤드가 원시 요청 대비 {ratio:.2f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — symbol 파싱/거래소 코드 조회에 의도치 않은 "
        "무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 — order_dispatch._split_overseas_symbol 가드 제거
# ---------------------------------------------------------------------------

_GUARD = (
    '    if ":" not in symbol:\n'
    "        raise FatalExchangeError(\n"
    "            \"해외주식/ETF/ETN 주문의 symbol은 'OVRS_EXCG_CD:종목코드' 형식이어야 \"\n"
    "            f\"합니다(예: 'NASD:AAPL'): {symbol!r}\"\n"
    "        )\n"
)
_MUTATED = ""


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


def test_pytest_gate_turns_red_when_overseas_symbol_prefix_guard_is_removed(
    tmp_path: Path,
) -> None:
    """`_split_overseas_symbol`의 콜론 형식 가드(fail-closed)를 자식
    pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
    `test_kis_capability_matrix.py::
    test_place_order_rejects_overseas_symbol_without_exchange_prefix`가
    green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다 — 가드 없이는
    콜론 없는 symbol이 `FatalExchangeError`가 아니라 `ValueError`(unpack
    실패)로 새 나간다."""
    target_test = (
        "tests/integration/exchanges/test_kis_capability_matrix.py::"
        "test_place_order_rejects_overseas_symbol_without_exchange_prefix"
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

    plugin_module_name = "_mutate_overseas_symbol_prefix_guard"
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


# ---------------------------------------------------------------------------
# 4) 다중 인스턴스/리플레이 적대적 증명
# ---------------------------------------------------------------------------


async def test_concurrent_orders_across_exchanges_never_cross_contaminate_codes() -> None:
    """어댑터 인스턴스 하나(토큰 캐시/락 공유)를 여러 거래소가 동시에 써도
    각 요청의 `OVRS_EXCG_CD`가 자기 거래소 코드와 정확히 일치한다 — 동시
    코루틴 사이에 거래소 코드가 섞이는 경쟁 상태가 없음을 증명한다."""
    exchanges = ["NASD", "NYSE", "AMEX", "SEHK", "SHAA", "SZAA", "TKSE", "VNSE", "HASE"]
    captured: list[httpx.Request] = []
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            await asyncio.sleep(0.005)  # 실제 왕복처럼 컨텍스트 스위치 유발
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        async with lock:
            captured.append(request)
        await asyncio.sleep(0.001)
        order_output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": order_output})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    adapter = KISAdapter(
        "app", "secret", "12345678", "01",
        is_paper_trading=True, http_client=http_client, sleep_fn=_instant_sleep,
    )

    async def place(exchange: str) -> str:
        # symbol의 venue_symbol에도 거래소명을 그대로 심어(PDNO가 exchange와
        # 같아지게) 두 개의 서로 다른 코드 경로(symbol 파싱 → PDNO,
        # `_exchange_codes` 조회 → OVRS_EXCG_CD)가 같은 호출의 같은 exchange
        # 값에서 나왔는지 응답 본문만 보고 교차검증할 수 있게 한다.
        order = _order(AssetClass.US_EQUITY, f"{exchange}:{exchange}")
        result = await dispatch_place_order(adapter, order)
        assert result.exchange_order_id == "ORG:1"
        return exchange

    results = await asyncio.gather(*(place(exchange) for exchange in exchanges))
    assert sorted(results) == sorted(exchanges)

    assert len(captured) == len(exchanges)
    seen_exchanges = set()
    for request in captured:
        body = json.loads(request.content)
        # PDNO(symbol 파싱 경로)와 OVRS_EXCG_CD(_exchange_codes 조회 경로)가
        # 항상 같은 값이어야 한다 — 둘 중 하나라도 동시 호출 사이에 섞였다면
        # 여기서 어긋난다.
        assert body["PDNO"] == body["OVRS_EXCG_CD"]
        seen_exchanges.add(body["OVRS_EXCG_CD"])
    assert seen_exchanges == set(exchanges)  # 9개 전부, 중복/누락 없음


async def test_concurrent_overseas_and_domestic_calls_issue_token_exactly_once() -> None:
    """토큰 미보유 상태에서 해외주식/국내주식 시세조회를 동시에 10개
    섞어 호출해도 토큰 발급 엔드포인트는 정확히 1회만 불린다(double-checked
    locking, `_token_lock`) — 해외 진입점을 추가해도 기존 단일화 보장이
    깨지지 않음을 확인한다."""
    token_calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            token_calls["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _QUOTE_PATH:
            output = {"last": "10.5", "tvol": "1"}
            return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})
        if request.url.path == "/uapi/domestic-stock/v1/quotations/inquire-price":
            output = {"stck_prpr": "70000", "acml_vol": "1"}
            return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})
        if request.url.path == "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn":
            book_output = {"askp1": "70100", "bidp1": "69900"}
            return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output1": book_output})
        raise AssertionError(f"예상치 못한 경로: {request.url.path}")

    adapter = _make_paper_adapter(handler)

    calls = [adapter.get_overseas_ticker("AAPL", "NASD") for _ in range(5)] + [
        adapter.get_ticker("005930") for _ in range(5)
    ]
    await asyncio.gather(*calls)

    assert token_calls["n"] == 1


async def test_replay_same_order_object_is_not_deduplicated_adapter_sends_it_twice() -> None:
    """어댑터 docstring이 명시하듯(adapter.py) KIS는 client_order_id 개념이
    없어 재전송을 걸러낼 방법이 구조적으로 없다 — 같은 Order 객체를 두 번
    보내면 (OMS 계층의 멱등키 보호 없이) 어댑터 혼자서는 그대로 두 번
    나간다는 사실을 사실로 남긴다(버그 아님, 이 어댑터 레이어의 설계
    한계 — 멱등성은 상위 OMS 몫)."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": str(business_calls["n"])}
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    adapter = _make_paper_adapter(handler)
    order = _order(AssetClass.US_EQUITY, "AAPL")

    first = await adapter.place_overseas_order(order, "NASD")
    second = await adapter.place_overseas_order(order, "NASD")

    assert business_calls["n"] == 2  # 재전송이 그대로 두 번째 HTTP 호출로 나감
    assert first.exchange_order_id != second.exchange_order_id  # 거래소가 서로 다른 주문으로 채번


async def test_live_configured_subclass_is_blocked_from_overseas_order_mutation() -> None:
    """방어 심화 검증 — 정상 경로로는 절대 만들어질 수 없는 상태
    (`is_paper_trading=False`)를 강제로 재현해도 `require_paper_sandbox`가
    `place_overseas_order`/`cancel_overseas_order`를 실제로 막는지
    확인한다(파일 스콥이 place_order 분기만 다루던 task-1786과 달리,
    해외주식 전용 메서드 자체에 대한 방어를 직접 확인)."""

    class _LiveConfiguredKISAdapter(KISAdapter):
        @property
        def is_paper_trading(self) -> bool:
            return False

    captured: list[httpx.Request] = []
    adapter = _LiveConfiguredKISAdapter(
        "app", "secret", "12345678", "01",
        is_paper_trading=True,  # 생성자 값은 무시되도록 프로퍼티를 오버라이드했다
        http_client=httpx.AsyncClient(
            base_url="https://openapivts.koreainvestment.com:29443",
            transport=httpx.MockTransport(_fast_success_handler(captured)),
        ),
        sleep_fn=_instant_sleep,
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.place_overseas_order(_order(AssetClass.US_EQUITY, "AAPL"), "NASD")

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.cancel_overseas_order(
            "ORG:1", "AAPL", "NASD", original_quantity=Decimal("1")
        )

    assert captured == []  # 두 호출 다 HTTP 요청 자체가 나가지 않았다
