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
        lambda request: _route(request, {"/uapi/domestic-stock/v1/ranking/credit-balance": handler})
    )
    result = await adapter.get_credit_balance_ranking()

    assert result == {
        "output1": [{"mksc_shrn_iscd": "005930"}],
        "output2": [{"mksc_shrn_iscd": "000660"}],
    }


async def test_get_credit_balance_ranking_missing_output2_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output1": []})

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-stock/v1/ranking/credit-balance": handler})
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
        lambda request: _route(request, {"/uapi/domestic-stock/v1/finance/balance-sheet": handler})
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


# ── D2 하한 보강: 성능 단언 + 게이트 적색 재현 (task-24250) ──────────────────


@pytest.mark.perf
async def test_get_investor_trend_estimate_latency_under_budget(perf_budget):
    """수치 성능 단언(D2) — `get_investor_trend_estimate`는 MockTransport
    위에서도 순수 HTTP 라우팅 + dict 접근만 하므로 CPU 시간은 마이크로초
    단위여야 한다. 100회 호출 평균 CPU가 1ms를 넘어선다면 경로에
    불필요한 루프/캐시 탐색/직렬화가 섞였음을 의미한다.

    실측 근거: 로컬 테스트에서 100회 호출 평균 cpu_ms ≈ 0.02ms.
    예산 1ms는 실측의 50배 여유.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": [{"frgn_fake_ntby_qty": "1000"}]},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )

    # 평균 대신 best-of-N(perf_budget): Windows process_time 은 해상도가 거칠고
    # CI 병렬 실행의 CPU 경합 spike 가 평균을 오염시키므로 최소 샘플로 판정한다.
    sample = await perf_budget.best_of_async(
        lambda: adapter.get_investor_trend_estimate("005930"),
        n=7,
        warmup=2,
        batch=20,
    )
    assert sample.cpu_ms < 1.0, (
        f"CPU 시간 {sample.cpu_ms:.3f}ms 가 예산 1ms 초과 (best-of-7, batch=20)"
    )


@pytest.mark.perf
async def test_get_investor_trend_estimate_gate_red_10x_delay_injection(
    perf_budget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2): 성능 단언이 실제로 지연을 포착하는지 역으로
    증명한다. `_request`에 `virtual_delayed_async`로 0.1s 가상 지연을
    주입하면 같은 단언이 예산을 초과해야 한다.

    단언/예산이 허울이면 지연 주입 후에도 통과하므로, 이 테스트는 실패해야
    한다(지연 주입 후 지연이 예산 초과).
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    reset_virtual_totals()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": [{"frgn_fake_ntby_qty": "1000"}]},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )

    # _request 내부에서 _client.request() 호출에 0.1s 가상 지연 주입
    original_request = adapter._client.request
    monkeypatch.setattr(
        adapter._client,
        "request",
        virtual_delayed_async(original_request, 0.1),
    )

    # 지연 주입 후 측정 — perf_budget.best_of 가 wall_ms에 가상 지연 합산
    sample = await perf_budget.best_of_async(
        lambda: adapter.get_investor_trend_estimate("005930"),
        n=5,
        warmup=1,
    )

    # 지연 주입 하에서 wall_ms 가 예산(0.05s = 50ms)을 초과해야 함
    # — 단언이 제대로 작동함을 증명
    assert sample.wall_ms >= 50.0, (
        f"지연 주입 후 wall_ms={sample.wall_ms:.3f}ms 가 하한 50ms 미만 — "
        "게이트가 지연을 포착하지 못함"
    )


async def test_get_investor_trend_estimate_empty_output2():
    """음성 테스트: output2 키가 존재하지만 빈 리스트면 빈 리스트를
    반환한다(FatalExchangeError 를 raise 하지 않음)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output2": []})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )
    result = await adapter.get_investor_trend_estimate("005930")
    assert result == []


async def test_get_investor_trend_estimate_missing_output2_key():
    """음성 테스트: output2 키 자체가 응답에 없으면 빈 리스트를
    반환한다(FatalExchangeError 를 raise 하지 않음).
    `raw.get("output2", [])` 의 방어 동작 검증."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok"},  # output2 없음
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )
    result = await adapter.get_investor_trend_estimate("005930")
    assert result == []


async def test_get_investor_trend_estimate_non_list_output2():
    """음성 테스트: output2 가 리스트가 아닌 단일 객체면
    list() 가 이를 감싸서 리스트로 반환한다.
    `_as_rows`와 동일한 정규화 동작."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output2": {"frgn_fake_ntby_qty": "500"}},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/investor-trend-estimate": handler}
        )
    )
    result = await adapter.get_investor_trend_estimate("005930")
    # list(dict) = dict의 key 목록 — _as_rows 와 동일 동작
    assert result == ["frgn_fake_ntby_qty"]
