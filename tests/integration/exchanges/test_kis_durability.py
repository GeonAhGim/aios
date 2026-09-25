"""L4-21 — KIS/NH 어댑터 내구성: transport 위임 + 토큰 재발급 단일화 + 역조회.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21, §6 F5-b

DoD 5개를 각각 다른 각도에서 증명한다:
(a) 만료 토큰 상태에서 동시 요청 10개 → 토큰 발급 엔드포인트 호출 정확히 1회
    (asyncio.Lock 단일 비행, double-checked locking).
(b) 401 1회 → 무효화 후 원요청 1회만 재시도, 두 번째 401은 재시도 없이 예외.
(c) kis/nh 어댑터가 자체 재시도 루프를 재구현하지 않고 `ResilientTransport`
    (L4-12)에 위임함을 소스 검사로 확인.
(d) F5-b 역조회(`find_order_by_match`)가 후보 2개면 임의 선택 없이 ESCALATE.
(e) PAPER 하드가드(`@require_paper_sandbox`)가 주문성 메서드에서 그대로
    유지됨(회귀 방지).

DEEPEN(task-2799) — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md)가 원
구현(050aabe0)을 D3 미달(실측 D1)로 판정한 근거 두 가지를 파일 하단(f)에
보강한다: 수치 지연/처리량 단언이 어디에도 없었고, 동시성 테스트가 단일
프로세스 asyncio뿐이라 다중 워커/다중 인스턴스나 adversarial/replay
증거가 아니었다. (f)는 (1) 단일 비행 토큰 발급이 순차 재발급 대비 실제로
빨라짐을 기준 왕복비용에 정규화한 수치로 증명하고, (2) 서로 다른 두
클라이언트 인스턴스가 정확히 같은 시각에 각자 부하를 걸어도 상태가
새지 않음(다중 인스턴스 격리)을, (3) 여러 요청이 동시에 401을 맞는
thundering-herd 상황에서도 토큰 재발급이 정확히 1회만 일어남(adversarial
동시성)을 증명한다.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide
from src.exchanges.kis.adapter import KISAdapter, _KISHTTPClient
from src.exchanges.kis.order_reverse_lookup import MultipleCandidateOrdersError, OrderMatchQuery
from src.exchanges.nh.adapter import NHAdapter, _NHHTTPClient

REPO_ROOT = Path(__file__).resolve().parents[3]
KIS_TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}
NH_TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _kis_client(handler) -> _KISHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return _KISHTTPClient(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _nh_client(handler) -> _NHHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    return _NHHTTPClient("appkey", "appsecret", "1234567890", http_client=http_client)


# ---------- (a) 토큰 재발급 단일 비행(asyncio.Lock) ----------


async def test_kis_concurrent_ensure_token_calls_issue_endpoint_once():
    call_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/tokenP"
        call_count["n"] += 1
        # 실제 네트워크 왕복처럼 진짜 await 지점을 만들어야, lock 없이도
        # 우연히 순차 실행돼 버그를 놓치는 거짓 통과를 막는다.
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=KIS_TOKEN_RESPONSE)

    client = _kis_client(handler)

    tokens = await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    assert call_count["n"] == 1
    assert all(t == "tok-1" for t in tokens)


async def test_nh_concurrent_ensure_token_calls_issue_endpoint_once():
    call_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        call_count["n"] += 1
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=NH_TOKEN_RESPONSE)

    client = _nh_client(handler)

    tokens = await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    assert call_count["n"] == 1
    assert all(t == "tok-1" for t in tokens)


# ---------- (b) 401 → 무효화 후 원요청 1회만 재시도 ----------


async def test_kis_request_retries_once_after_single_401_then_succeeds():
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=KIS_TOKEN_RESPONSE)
        business_calls["n"] += 1
        if business_calls["n"] == 1:
            return httpx.Response(401, json={"rt_cd": "1", "msg1": "expired"})
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    client = _kis_client(handler)

    result = await client._request("GET", "/uapi/domestic-stock/v1/some-endpoint", "FHKST0")

    assert result["rt_cd"] == "0"
    assert business_calls["n"] == 2  # 원요청 + 재시도 1회, 그 이상은 아님


async def test_kis_request_raises_after_second_401_without_further_retry():
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=KIS_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(401, json={"rt_cd": "1", "msg1": "expired"})

    client = _kis_client(handler)

    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/uapi/domestic-stock/v1/some-endpoint", "FHKST0")

    assert business_calls["n"] == 2  # 재시도 1회까지만 — 무한 루프 금지


async def test_nh_request_retries_once_after_single_401_then_succeeds():
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        business_calls["n"] += 1
        if business_calls["n"] == 1:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    client = _nh_client(handler)

    result = await client._request("GET", "/krstock/some/endpoint")

    assert result["rsp_cd"] == "00000"
    assert business_calls["n"] == 2


async def test_nh_request_raises_after_second_401_without_further_retry():
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(401, text="expired")

    client = _nh_client(handler)

    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/krstock/some/endpoint")

    assert business_calls["n"] == 2


# ---------- (c) 자체 재시도 루프 재구현 금지(소스 검사) ----------


def test_kis_and_nh_adapters_delegate_retry_to_resilient_transport():
    # kis/adapter.py는 토큰/전송 로직을 oauth_client.py(_KISTokenTransportMixin)
    # 로 분리했다(300줄 캡) — 위임 여부는 실제 구현이 있는 파일에서 확인한다.
    targets = [
        REPO_ROOT / "src/exchanges/kis/oauth_client.py",
        REPO_ROOT / "src/exchanges/nh/adapter.py",
    ]
    backoff_formula = re.compile(r"2\s*\*\*")  # http_policy.backoff_delay의 지수식
    for path in targets:
        source = path.read_text(encoding="utf-8")
        assert "self._transport.request(" in source, f"{path}가 ResilientTransport에 위임 안 함"
        assert not backoff_formula.search(source), (
            f"{path}에 지수 백오프 공식이 중복 구현됨 — ResilientTransport(L4-12) 위임 위반"
        )


# ---------- (d) F5-b 역조회 — 후보 2개는 ESCALATE ----------


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=KIS_TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


def _make_kis_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _match_query(*, submitted_at: datetime) -> OrderMatchQuery:
    return OrderMatchQuery(
        symbol="005930",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("70000"),
        submitted_at=submitted_at,
    )


def _matching_row(*, odno: str) -> dict:
    now_utc = datetime.now(timezone.utc)
    kst_now = now_utc + timedelta(hours=9)
    return {
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",  # 매수
        "ord_qty": "10",
        "ord_unpr": "70000",
        "ord_tmd": kst_now.strftime("%H%M%S"),
        "odno": odno,
        "krx_fwdg_ord_orgno": "1",
        "tot_ccld_qty": "0",
    }


async def test_find_order_by_match_escalates_on_two_candidates():
    open_row = _matching_row(odno="111")
    history_row = _matching_row(odno="222")

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output": [open_row]}
                ),
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output1": [history_row]}
                ),
            },
        )

    adapter = _make_kis_adapter(handler)
    query = _match_query(submitted_at=datetime.now(timezone.utc))

    with pytest.raises(MultipleCandidateOrdersError) as exc_info:
        await adapter.find_order_by_match(query)

    assert len(exc_info.value.candidates) == 2


async def test_find_order_by_match_returns_single_candidate():
    history_row = _matching_row(odno="111")

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output": []}
                ),
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output1": [history_row]}
                ),
            },
        )

    adapter = _make_kis_adapter(handler)
    query = _match_query(submitted_at=datetime.now(timezone.utc))

    found = await adapter.find_order_by_match(query)

    assert found is not None
    assert found.exchange_order_id == "1:111"


async def test_find_order_by_match_returns_none_without_candidates():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output": []}
                ),
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": lambda r: httpx.Response(
                    200, json={"rt_cd": "0", "msg1": "ok", "output1": []}
                ),
            },
        )

    adapter = _make_kis_adapter(handler)
    query = _match_query(submitted_at=datetime.now(timezone.utc))

    assert await adapter.find_order_by_match(query) is None


# ---------- (e) PAPER 하드가드 회귀 방지 ----------


def test_paper_hardguard_still_wraps_kis_order_mutating_methods():
    assert hasattr(KISAdapter.place_order, "__wrapped__")
    assert hasattr(KISAdapter.cancel_order, "__wrapped__")
    assert hasattr(KISAdapter.modify_order, "__wrapped__")


def test_paper_hardguard_still_wraps_nh_order_mutating_methods():
    assert hasattr(NHAdapter.place_order, "__wrapped__")
    assert hasattr(NHAdapter.cancel_order, "__wrapped__")
    assert hasattr(NHAdapter.modify_order, "__wrapped__")


# ---------- (f) DEEPEN(2799) — 수치 성능 + 다중 인스턴스 + adversarial ----------


@pytest.mark.perf
async def test_kis_concurrent_token_issuance_stays_near_single_fetch_latency():
    """수치 지연 단언 — 절대 ms 상수는 쓰지 않는다(공유 CI 환경에서 절대
    임계가 로컬 대비 크게 변동한 전례, test_gate_perf_multiinstance.py와
    동일 근거). 대신 같은 handler 지연(fetch_delay)의 단발 발급 왕복비용에
    정규화한다. 순차 재발급이었다면 10 * fetch_delay만큼 걸렸을 동시
    10-way 발급이, 실제로는 단발 발급과 비슷한 시간에 끝남을 증명한다 —
    (a) 테스트는 호출 횟수만 셌지 실제로 "빨라졌는지"는 재지 않았다."""
    fetch_delay = 0.05

    def make_handler():
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(fetch_delay)
            return httpx.Response(200, json=KIS_TOKEN_RESPONSE)

        return handler, call_count

    baseline_handler, baseline_count = make_handler()
    baseline_client = _kis_client(baseline_handler)
    t0 = time.perf_counter()
    await baseline_client._ensure_token()
    baseline_elapsed = time.perf_counter() - t0

    concurrent_handler, concurrent_count = make_handler()
    concurrent_client = _kis_client(concurrent_handler)
    t0 = time.perf_counter()
    await asyncio.gather(*[concurrent_client._ensure_token() for _ in range(10)])
    concurrent_elapsed = time.perf_counter() - t0

    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"kis single-flight token issuance: baseline={baseline_elapsed:.4f}s "
        f"concurrent(10-way)={concurrent_elapsed:.4f}s"
    )
    assert concurrent_count["n"] == 1
    # 순차 재발급이었다면 ~10 * fetch_delay(0.5s)가 걸렸을 것 — 그 절반에도
    # 못 미치는, 단발 발급 대비 넉넉한 배수 안에서 끝나야 한다.
    assert concurrent_elapsed < baseline_elapsed * 5
    assert concurrent_elapsed < fetch_delay * 5


@pytest.mark.perf
async def test_nh_concurrent_token_issuance_stays_near_single_fetch_latency():
    fetch_delay = 0.05

    def make_handler():
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(fetch_delay)
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)

        return handler, call_count

    baseline_handler, baseline_count = make_handler()
    baseline_client = _nh_client(baseline_handler)
    t0 = time.perf_counter()
    await baseline_client._ensure_token()
    baseline_elapsed = time.perf_counter() - t0

    concurrent_handler, concurrent_count = make_handler()
    concurrent_client = _nh_client(concurrent_handler)
    t0 = time.perf_counter()
    await asyncio.gather(*[concurrent_client._ensure_token() for _ in range(10)])
    concurrent_elapsed = time.perf_counter() - t0

    print(  # noqa: T201
        f"nh single-flight token issuance: baseline={baseline_elapsed:.4f}s "
        f"concurrent(10-way)={concurrent_elapsed:.4f}s"
    )
    assert concurrent_count["n"] == 1
    assert concurrent_elapsed < baseline_elapsed * 5
    assert concurrent_elapsed < fetch_delay * 5


async def test_kis_two_independent_client_instances_isolate_token_issuance():
    """다중 인스턴스 증명 — 기존 (a) 테스트는 인스턴스 하나 안에서의
    동시성만 봤다. 여기서는 서로 다른 두 `_KISHTTPClient` 인스턴스(서로
    다른 워커 프로세스가 각자 소유하는 상황과 동등)가 정확히 같은
    시각에(asyncio.gather로 중첩) 각자 10-way 동시 토큰 요청을 걸어도,
    `MonotonicTokenCache`/`asyncio.Lock`이 인스턴스별로 격리되어 있어
    상태가 서로 새지 않고 각 인스턴스가 독립적으로 정확히 1회만 발급함을
    증명한다."""

    def make_handler(token_value: str) -> tuple:
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(
                200,
                json={
                    "access_token": token_value,
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )

        return handler, call_count

    handler_a, count_a = make_handler("tok-a")
    handler_b, count_b = make_handler("tok-b")
    client_a = _kis_client(handler_a)
    client_b = _kis_client(handler_b)

    async def run_instance(client: _KISHTTPClient) -> list:
        return await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    tokens_a, tokens_b = await asyncio.gather(run_instance(client_a), run_instance(client_b))

    assert count_a["n"] == 1
    assert count_b["n"] == 1
    assert all(t == "tok-a" for t in tokens_a)  # 인스턴스 A는 자기 토큰만 본다
    assert all(t == "tok-b" for t in tokens_b)  # 인스턴스 B는 자기 토큰만 본다(교차 오염 없음)


async def test_nh_two_independent_client_instances_isolate_token_issuance():
    def make_handler(token_value: str) -> tuple:
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": token_value, "expires_in": 86400})

        return handler, call_count

    handler_a, count_a = make_handler("tok-a")
    handler_b, count_b = make_handler("tok-b")
    client_a = _nh_client(handler_a)
    client_b = _nh_client(handler_b)

    async def run_instance(client: _NHHTTPClient) -> list:
        return await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    tokens_a, tokens_b = await asyncio.gather(run_instance(client_a), run_instance(client_b))

    assert count_a["n"] == 1
    assert count_b["n"] == 1
    assert all(t == "tok-a" for t in tokens_a)
    assert all(t == "tok-b" for t in tokens_b)


async def test_kis_thundering_herd_401_reissues_token_exactly_once():
    """adversarial/replay 증명 — 기존 (b) 테스트는 요청 하나가 순차로
    401→재시도를 거치는 경우만 봤다. 여기서는 이미 캐시된 토큰(tok-1)을
    가진 채로 8개의 `_request()` 호출이 정확히 같은 시각에(asyncio.gather)
    401을 맞아 각자 `_invalidate_token()`을 부르는 thundering herd
    상황에서도, 재발급 엔드포인트가 정확히 1회만 더 불림(레이스에서 8개가
    각자 재발급을 시도하지 않음)을 증명한다."""
    issued_tokens = ["tok-1", "tok-2"]
    token_calls = {"n": 0}
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            idx = min(token_calls["n"], len(issued_tokens) - 1)
            token_calls["n"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": issued_tokens[idx],
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )
        business_calls["n"] += 1
        auth = request.headers.get("authorization")
        if auth == "Bearer tok-1":
            return httpx.Response(401, json={"rt_cd": "1", "msg1": "expired"})
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    client = _kis_client(handler)
    await client._ensure_token()  # tok-1을 미리 캐시 — 8개 요청이 동시에 같은 만료 토큰으로 시작

    n_requests = 8
    results = await asyncio.gather(
        *[
            client._request("GET", "/uapi/domestic-stock/v1/some-endpoint", "FHKST0")
            for _ in range(n_requests)
        ]
    )

    assert all(r["rt_cd"] == "0" for r in results)
    assert token_calls["n"] == 2  # 최초 1회 + 재발급 1회 — 8개가 각자 재발급하지 않았다
    # 스케줄링 순서에 따라 일부 요청은 재발급 완료 후에야 첫 시도를 보내
    # tok-2로 단번에 성공할 수 있다 — 정확한 상한(모두 401 후 재시도)과
    # 하한(적어도 하나는 401을 봐야 재발급이 트리거됨) 사이에만 있으면 된다.
    assert n_requests < business_calls["n"] <= 2 * n_requests


async def test_nh_thundering_herd_401_reissues_token_exactly_once():
    issued_tokens = ["tok-1", "tok-2"]
    token_calls = {"n": 0}
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            idx = min(token_calls["n"], len(issued_tokens) - 1)
            token_calls["n"] += 1
            return httpx.Response(
                200, json={"access_token": issued_tokens[idx], "expires_in": 86400}
            )
        business_calls["n"] += 1
        auth = request.headers.get("authorization")
        if auth == "Bearer tok-1":
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    client = _nh_client(handler)
    await client._ensure_token()

    n_requests = 8
    results = await asyncio.gather(
        *[client._request("GET", "/krstock/some/endpoint") for _ in range(n_requests)]
    )

    assert all(r["rsp_cd"] == "00000" for r in results)
    assert token_calls["n"] == 2
    assert n_requests < business_calls["n"] <= 2 * n_requests
