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
"""
from __future__ import annotations

import asyncio
import re
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
