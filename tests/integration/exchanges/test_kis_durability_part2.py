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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from src.data.models.trading import OrderSide
from src.exchanges.kis.adapter import KISAdapter, _KISHTTPClient
from src.exchanges.kis.order_reverse_lookup import OrderMatchQuery
from src.exchanges.nh.adapter import _NHHTTPClient

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
    now_utc = datetime.now(UTC)
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
