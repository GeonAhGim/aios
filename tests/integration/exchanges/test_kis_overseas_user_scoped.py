"""task-1776 L4-32 — 해외주식 경로로 들어온 시세의 USER_SCOPED 태깅.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-32,
ADR-2026-09-06-H D7. 사용자 소유 KIS 연결로 받은 해외 시세는 재배포
권리가 연결 소유자 본인에게만 있다 — 그 사실을 `Ticker.redistribution_
scope`에 구조로 남겨, 공유 캐시·스크리너가 나중에 이 값을 보고 차단할
수 있게 한다(값을 소비해 실제로 막는 로직은 별도 리프 몫, 이 리프는
태깅만 증명한다).
"""
from __future__ import annotations

import httpx
import pytest

from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.overseas_stock_mixin import _EXCHANGES

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
_DOMESTIC_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
_DOMESTIC_BOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"


def _make_paper_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        if path == _QUOTE_PATH:
            return httpx.Response(
                200, json={"rt_cd": "0", "msg1": "OK", "output": {"last": "10.5", "tvol": "100"}}
            )
        if path == _DOMESTIC_PRICE_PATH:
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "OK",
                    "output": {"stck_prpr": "70000", "acml_vol": "1000"},
                },
            )
        if path == _DOMESTIC_BOOK_PATH:
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "OK",
                    "output1": {"askp1": "70100", "bidp1": "69900"},
                },
            )
        raise AssertionError(f"예상치 못한 경로: {path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


@pytest.mark.parametrize("exchange", sorted(_EXCHANGES))
async def test_overseas_ticker_is_tagged_user_scoped(exchange: str) -> None:
    adapter = _make_paper_adapter()

    ticker = await adapter.get_overseas_ticker("AAPL", exchange)

    assert ticker.redistribution_scope == "USER_SCOPED"


async def test_domestic_ticker_is_not_tagged_user_scoped() -> None:
    """negative — 이 태깅은 해외주식 경로 전용이다. 국내주식 시세까지
    같이 태깅되면 D7의 "이 경로로 들어온 시세" 범위를 벗어난다."""
    adapter = _make_paper_adapter()

    ticker = await adapter.get_ticker("005930")

    assert ticker.redistribution_scope is None
