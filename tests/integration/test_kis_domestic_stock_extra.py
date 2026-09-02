"""02d_kis_api_full_spec_v1.md §3 통합테스트 — 국내주식 조회 확장(P1).

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""
import httpx

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


async def test_get_investor_trend_estimate_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "HHPTJ04160200"
        assert request.url.params["MKSC_SHRN_ISCD"] == "005930"
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": [{"frgn_fake_ntby_qty": "1000"}]},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )
    result = await adapter.get_investor_trend_estimate("005930")

    assert result == [{"frgn_fake_ntby_qty": "1000"}]


async def test_get_financial_ratio_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST66430300"
        assert request.url.params["FID_DIV_CLS_CODE"] == "0"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"stac_yymm": "202412"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
        )
    )
    result = await adapter.get_financial_ratio("005930")

    assert result == [{"stac_yymm": "202412"}]


async def test_get_investor_trading_by_stock_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST01010900"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": {"frgn_ntby_qty": "500"}}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-investor": handler}
        )
    )
    result = await adapter.get_investor_trading_by_stock("005930")

    assert result == {"frgn_ntby_qty": "500"}


async def test_get_dividend_disclosures_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "HHKDB669102C0"
        assert request.url.params["SHT_CD"] == "005930"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"record_date": "20260101"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-stock/v1/ksdinfo/dividend": handler})
    )
    result = await adapter.get_dividend_disclosures(symbol="005930")

    assert result == [{"record_date": "20260101"}]


async def test_get_program_trade_daily_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHPPG04600001"
        assert request.url.params["FID_MRKT_CLS_CODE"] == "K"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"bass_dt": "20260901"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/comp-program-trade-daily": handler}
        )
    )
    result = await adapter.get_program_trade_daily("K")

    assert result == [{"bass_dt": "20260901"}]
