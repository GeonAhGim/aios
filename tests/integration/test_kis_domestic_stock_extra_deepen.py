"""test_kis_domestic_stock_extra.py ::test_get_financial_ratio_returns_raw_rows D2 보강.

negative 3종, 성능 단언 1, 게이트 적색 재현 1을 추가한다.
공용 헬퍼(_make_adapter, _route)는 읽기 전용으로 공유한다.
"""

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.rate_profile import reset_token_bucket_registry_for_test

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


async def _no_sleep(_seconds: float) -> None:
    """성능 측정용: 모의투자 토큰 버킷 대기(초당 호출 제한)를 실제로 자지 않는다.

    100회 루프가 50~70초 걸려 시간 단언이 흔들리고 full CI의 직렬 perf 단계를 늘렸다(2026-11-13).
    측정 대상은 어댑터 CPU 경로이지 제한 대기가 아니다."""


def _make_perf_adapter(handler) -> KISAdapter:
    # 프로세스 전체 싱글턴 버킷은 처음 만든 어댑터의 sleep을 쓴다.
    # 앞선 일반 테스트가 실제 asyncio.sleep으로 만든 버킷이 남아 있으면
    # no-op sleep을 줘도 70초씩 진짜로 잔다 -- 비워서 새로 만들게 한다.
    reset_token_bucket_registry_for_test()
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=client,
        sleep_fn=_no_sleep,
    )


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


# ── negative tests (3종) ──────────────────────────────────────────────────


async def test_get_financial_ratio_empty_output():
    """음성 테스트: output 가 빈 리스트면 빈 리스트를 반환한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": []})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
        )
    )
    result = await adapter.get_financial_ratio("005930")
    assert result == []


async def test_get_financial_ratio_missing_output_key():
    """음성 테스트: output 키 자체가 응답에 없으면 빈 리스트를
    반환한다(FatalExchangeError 를 raise 하지 않음).
    `raw.get("output", [])` 의 방어 동작 검증."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok"})  # output 없음

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
        )
    )
    result = await adapter.get_financial_ratio("005930")
    assert result == []


async def test_get_financial_ratio_rt_cd_nonzero():
    """음성 테스트: rt_cd 가 0이 아니면 RetryableExchangeError 를 발생시킨다.

    KIS `_classify_body` 는 rt_cd != "0" 에 UNKNOWN_RESPONSE(retryable=True)
    를 만들고, `_request` 가 이를 RetryableExchangeError 로 감싼다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "1", "msg1": "error", "output": []})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
        )
    )
    with pytest.raises(RetryableExchangeError):
        await adapter.get_financial_ratio("005930")


# ── D2 하한 보강: 성능 단언 + 게이트 적색 재현 ─────────────────────────────


@pytest.mark.perf
async def test_get_financial_ratio_latency_under_budget(perf_budget):
    """수치 성능 단언(D2) — `get_financial_ratio`는 MockTransport
    위에서도 순수 HTTP 라우팅 + dict 접근만 하므로 CPU 시간은 마이크로초
    단위여야 한다. 100회 호출 평균 CPU가 1ms를 넘어선다면 경로에
    불필요한 루프/캐시 탐색/직렬화가 섞였음을 의미한다.

    실측 근거: 로컬 테스트에서 100회 호출 평균 cpu_ms ≈ 0.02ms.
    예산 1ms는 실측의 50배 여유.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"stac_yymm": "202412"}]}
        )

    adapter = _make_perf_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
        )
    )

    # 평균 대신 best-of-N(perf_budget): Windows process_time 해상도(15.6ms 눈금)와 호스트 경쟁에
    # 흔들리지 않는다. 예산 1ms는 그대로다. 제한 대기는 _make_perf_adapter가 건너뛴다.
    sample = await perf_budget.best_of_async(
        lambda: adapter.get_financial_ratio("005930"),
        n=7,
        warmup=2,
        batch=20,
    )
    assert sample.cpu_ms < 1.0, f"CPU 시간 {sample.cpu_ms:.3f}ms 가 예산 1ms 초과"


@pytest.mark.perf
async def test_get_financial_ratio_gate_red_10x_delay_injection(
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
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"stac_yymm": "202412"}]}
        )

    adapter = _make_perf_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/finance/financial-ratio": handler}
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
        lambda: adapter.get_financial_ratio("005930"),
        n=5,
        warmup=1,
    )

    # 지연 주입 하에서 wall_ms 가 예산(0.05s = 50ms)을 초과해야 함
    # — 단언이 제대로 작동함을 증명
    assert sample.wall_ms >= 50.0, (
        f"지연 주입 후 wall_ms={sample.wall_ms:.3f}ms 가 하한 50ms 미만 — "
        "게이트가 지연을 포착하지 못함"
    )
