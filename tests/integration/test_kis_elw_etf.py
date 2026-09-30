"""02d_kis_api_full_spec_v1.md §4 통합테스트 — ELW/ETF/ETN.

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.kis.adapter import KISAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


# ─── positive cases (baseline) ────────────────────────────────────────────────


async def test_get_elw_price_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKEW15010000"
        assert request.url.params["FID_COND_MRKT_DIV_CODE"] == "W"
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "150", "acml_vol": "500"}},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-elw-price": handler}
        )
    )
    ticker = await adapter.get_elw_price("58J300")

    assert ticker.price == Decimal("150")


async def test_get_etf_price_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHPST02400000"
        assert request.url.params["FID_COND_MRKT_DIV_CODE"] == "J"
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "10000", "acml_vol": "2000"}},
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/etfetn/v1/quotations/inquire-price": handler})
    )
    ticker = await adapter.get_etf_price("069500")

    assert ticker.price == Decimal("10000")


# ─── negative tests (I-07: hard-fail 조건은 도메인 코드가 실제로 FAIL) ────────


async def test_get_elw_price_rejects_rt_cd_nonzero():
    """rt_cd != "0"이면 _classify_body가 ExchangeError(retryable=True)를
    반환하고, 호출부(_request)가 이를 RetryableExchangeError로 승격해
    던져야 한다(I-07) — 조용히 0가/빈 값으로 넘어가지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "-1",
                "msg1": "ELW 종목코드 없음",
                "output": {},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-elw-price": handler}
        )
    )

    with pytest.raises(RetryableExchangeError, match="KIS API 오류"):
        await adapter.get_elw_price("INVALID")


async def test_get_etf_price_rejects_rt_cd_nonzero():
    """rt_cd != "0"이면 _classify_body가 ExchangeError(retryable=True)를
    반환하고, 호출부(_request)가 이를 RetryableExchangeError로 승격해
    던져야 한다(I-07) — 조용히 0가/빈 값으로 넘어가지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "-1",
                "msg1": "ETF 종목코드 없음",
                "output": {},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/etfetn/v1/quotations/inquire-price": handler})
    )

    with pytest.raises(RetryableExchangeError, match="KIS API 오류"):
        await adapter.get_etf_price("INVALID")


async def test_get_elw_price_rejects_empty_output():
    """output 필드가 비어 있으면 price=Decimal("0")가 되므로
    호출부는 0가를 감지해 별도 검증 게이트를 거치게 된다.
    rt_cd="0"이지만 output.stck_prpr 누락 케이스."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {}},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-elw-price": handler}
        )
    )
    ticker = await adapter.get_elw_price("58J300")

    # stck_prpr 누락 → 기본값 "0" → Decimal("0")
    assert ticker.price == Decimal("0")


# ─── failure injection tests (I-10: 배선 증명 테스트) ────────────────────────


async def test_get_elw_price_injects_network_error():
    """의존성(httpx)에서 네트워크 예외가 재시도 한도까지 반복되면
    조용히 성공하지 않고 RetryableExchangeError로 승격되어
    전파된다(I-10) — ResilientTransport가 httpx.TransportError를
    삼키고 성공으로 위장하지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        raise httpx.ConnectError("connection refused")

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError, match="connection refused"):
        await adapter.get_elw_price("58J300")


async def test_get_etf_price_injects_invalid_json_response():
    """KIS API가 JSON이 아닌 응답을 반환하면 _classify_body가
    UNKNOWN_RESPONSE ExchangeError(retryable=True)를 만들고, 호출부가
    이를 RetryableExchangeError로 승격해 던진다(I-10)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/etfetn/v1/quotations/inquire-price": handler})
    )

    with pytest.raises(RetryableExchangeError, match="KIS 응답이 JSON이 아님"):
        await adapter.get_etf_price("069500")


async def test_get_elw_price_injects_token_refresh_failure():
    """토큰 엔드포인트가 401(AUTH)을 반환하면 FatalExchangeError가
    발생해야 한다 — AUTH는 재시도 불가로 분류되므로(_RETRYABLE_KINDS에
    없음) `_fetch_token`이 이를 잡아 재시도해도 소용없는 오류로
    승격한다(I-10, fail-closed)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid app key"})

    adapter = _make_adapter(handler)

    with pytest.raises(FatalExchangeError, match="KIS 토큰 발급 실패"):
        await adapter.get_elw_price("58J300")


# ─── performance assertion (task-4084 DEEPEN 기준) ──────────────────────────


@pytest.mark.perf
async def test_get_elw_price_completes_within_budget(perf_budget):
    """시세 조회 1회당 평균 < 1500ms (모의 transport + 실제 TR 그룹
    rate-limit 버킷 기준). rate_profile.py의 토큰 버킷이 실거래소 제한을
    지키려고 호출 사이에 실제 asyncio.sleep 대기를 넣으므로(관측 시
    안정 상태 호출당 ~500ms), 이 값을 그대로 0에 가까운 값으로 요구하면
    설계된 rate-limit 동작 자체를 위반해야 통과하는 거짓 그린이 된다.
    Budget: ADR-2026-09-09-C Decision 1 — 관측치의 3배 여유로, 재시도
    폭주·데드락 등 실제 회귀는 여전히 잡아낸다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "150", "acml_vol": "500"}},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-elw-price": handler}
        )
    )

    iterations = 5
    timings_ms = []
    for _ in range(iterations):
        measured = await perf_budget.sample_async(lambda: adapter.get_elw_price("58J300"))
        timings_ms.append(measured.wall_ms)
    elapsed_ms = sum(timings_ms) / len(timings_ms)

    assert elapsed_ms < 1500, f"avg {elapsed_ms:.1f}ms exceeds 1500ms budget"


@pytest.mark.perf
async def test_get_etf_price_completes_within_budget(perf_budget):
    """시세 조회 1회당 평균 < 1500ms (모의 transport + 실제 TR 그룹
    rate-limit 버킷 기준, 근거는 test_get_elw_price_completes_within_budget
    참고)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "10000", "acml_vol": "2000"}},
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/etfetn/v1/quotations/inquire-price": handler})
    )

    iterations = 5
    timings_ms = []
    for _ in range(iterations):
        measured = await perf_budget.sample_async(lambda: adapter.get_etf_price("069500"))
        timings_ms.append(measured.wall_ms)
    elapsed_ms = sum(timings_ms) / len(timings_ms)

    assert elapsed_ms < 1500, f"avg {elapsed_ms:.1f}ms exceeds 1500ms budget"
