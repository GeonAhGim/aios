"""02d_kis_api_full_spec_v1.md §3 통합테스트 — 국내주식 조회 확장(P1).

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""
import httpx
import pytest

from src.core.exceptions import FatalExchangeError
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


async def test_get_credit_balance_ranking_returns_both_outputs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST17010000"
        assert request.url.params["FID_RANK_SORT_CLS_CODE"] == "0"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": [{"mksc_shrn_iscd": "005930"}],
                "output2": [{"mksc_shrn_iscd": "000660"}],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/ranking/credit-balance": handler}
        )
    )
    result = await adapter.get_credit_balance_ranking()

    assert result == {
        "output1": [{"mksc_shrn_iscd": "005930"}],
        "output2": [{"mksc_shrn_iscd": "000660"}],
    }


async def test_get_credit_balance_ranking_missing_output2_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output1": []}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/ranking/credit-balance": handler}
        )
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_credit_balance_ranking()


async def test_get_daily_credit_balance_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHPST04760000"
        assert request.url.params["FID_INPUT_ISCD"] == "005930"
        assert request.url.params["FID_INPUT_DATE_1"] == "20260901"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"crdt_rmnd_qty": "10"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/daily-credit-balance": handler}
        )
    )
    result = await adapter.get_daily_credit_balance("005930", settle_date="20260901")

    assert result == [{"crdt_rmnd_qty": "10"}]


async def test_get_daily_credit_balance_missing_output_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok"})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/daily-credit-balance": handler}
        )
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_daily_credit_balance("005930", settle_date="20260901")


async def test_get_lendable_by_company_returns_both_outputs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTSC2702R"  # 모의투자 치환 확인
        assert request.url.params["THCO_STLN_PSBL_YN"] == "Y"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": {"pdno": "005930"},
                "output2": [],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/lendable-by-company": handler}
        )
    )
    result = await adapter.get_lendable_by_company()

    assert result == {"output1": [{"pdno": "005930"}], "output2": []}


async def test_get_credit_by_company_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHPST04770000"
        assert request.url.params["fid_slct_yn"] == "0"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"pdno": "005930"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/credit-by-company": handler}
        )
    )
    result = await adapter.get_credit_by_company()

    assert result == [{"pdno": "005930"}]


async def test_get_credit_by_company_missing_output_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok"})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/credit-by-company": handler}
        )
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_credit_by_company()


async def test_get_financial_balance_sheet_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST66430100"
        assert request.url.params["fid_input_iscd"] == "000660"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"total_aset": "1000"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/balance-sheet": handler}
        )
    )
    result = await adapter.get_financial_balance_sheet("000660")

    assert result == [{"total_aset": "1000"}]


async def test_get_income_statement_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST66430200"
        assert request.url.params["FID_DIV_CLS_CODE"] == "1"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"sale_account": "500"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/income-statement": handler}
        )
    )
    result = await adapter.get_income_statement("005930", period_div_code="1")

    assert result == [{"sale_account": "500"}]


async def test_get_income_statement_missing_output_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok"})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/income-statement": handler}
        )
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_income_statement("005930")
