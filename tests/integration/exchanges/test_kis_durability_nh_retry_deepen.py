"""L4-21 DEEPEN — NH 401 재시도 단일화(`_request`)의 D2/D3 증거.

대상: test_kis_durability.py::test_nh_request_retries_once_after_single_401_then_succeeds
(원 파일이 500줄 임계 근처라 sibling 파일로 분리, CLAUDE.md §6-12).

추가 증거: negative(토큰 재발급 실패 / 5xx 비재시도 / 두 번째 401 재시도 상한),
failure injection(재발급 응답 오류·전송 오류), 성능 단언(best_of, 가상 지연 10배 주입 적색 재현).

성능 예산: 로컬 실측(MockTransport, 401->재발급->재시도 1왕복) best-of-5 wall 0.58ms
(async는 wall 기준). 예산 100ms = 실측의 30배 이상 여유(공유 CI 변동 흡수). 신설 예산이며 기존 예산 변경 없음.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.nh.adapter import _NHHTTPClient
from tests.integration.exchanges.test_kis_durability import NH_TOKEN_RESPONSE, _nh_client
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

OK_BODY = {"rsp_cd": "00000", "rsp_msg": "ok"}
BUDGET_MS = 100.0


def _flaky_handler(calls: dict, *, first_status: int = 401):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        calls["biz"] += 1
        if calls["biz"] == 1:
            return httpx.Response(first_status, text="expired")
        return httpx.Response(200, json=OK_BODY)

    return handler


async def test_nh_single_401_reissues_token_exactly_once_before_retry():
    calls = {"token": 0, "biz": 0}
    client = _nh_client(_flaky_handler(calls))

    result = await client._request("GET", "/krstock/some/endpoint")

    assert result["rsp_cd"] == "00000"
    assert calls == {"token": 2, "biz": 2}  # 최초 발급 1 + 401 후 재발급 1


async def test_nh_non_401_error_is_not_retried():
    calls = {"token": 0, "biz": 0}
    client = _nh_client(_flaky_handler(calls, first_status=400))

    with pytest.raises(Exception) as exc:  # noqa: PT011 — 400 분류는 어댑터 소관
        await client._request("GET", "/krstock/some/endpoint")

    assert calls["biz"] == 1, f"400은 재시도 금지: {exc.value!r}"


async def test_nh_401_with_token_reissue_rejected_fails_closed():
    biz = {"n": 0}
    token = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            token["n"] += 1
            if token["n"] == 1:
                return httpx.Response(200, json=NH_TOKEN_RESPONSE)
            return httpx.Response(401, text="invalid secret")
        biz["n"] += 1
        return httpx.Response(401, text="expired")

    client = _nh_client(handler)

    with pytest.raises(Exception):  # noqa: B017, PT011 — 재발급 거부는 성공으로 위장 금지
        await client._request("GET", "/krstock/some/endpoint")

    assert biz["n"] == 1  # 재발급 실패 후 원요청 재전송 없음


async def test_nh_failure_injection_transport_error_on_retry_propagates():
    biz = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        biz["n"] += 1
        if biz["n"] == 1:
            return httpx.Response(401, text="expired")
        raise httpx.ConnectError("injected network failure")

    client = _nh_client(handler)

    with pytest.raises(Exception) as exc:  # noqa: PT011
        await client._request("GET", "/krstock/some/endpoint")

    assert not isinstance(exc.value, AssertionError)
    # 401 재시도 1회 뒤 전송 오류는 ResilientTransport의 유한 재시도(실측 총 5회)로만 소진된다.
    assert 2 <= biz["n"] <= 5


async def test_nh_second_401_is_fatal_after_exactly_one_retry():
    calls = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        calls["biz"] += 1
        return httpx.Response(401, text="expired")

    client = _nh_client(handler)

    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/krstock/some/endpoint")

    assert calls["biz"] == 2


async def _one_retry_roundtrip():
    calls = {"token": 0, "biz": 0}
    client = _nh_client(_flaky_handler(calls))
    return await client._request("GET", "/krstock/some/endpoint")


@pytest.mark.perf
async def test_nh_401_retry_roundtrip_within_budget():
    sample = await PerfBudget().best_of_async(_one_retry_roundtrip, n=5, warmup=1)

    print(f"nh 401 retry roundtrip best-of-5 wall={sample.wall_ms:.3f}ms")  # noqa: T201
    assert sample.wall_ms < BUDGET_MS, PerfBudget().describe(sample, budget_ms=BUDGET_MS)


@pytest.mark.perf
async def test_nh_401_retry_budget_gate_red_under_10x_virtual_delay(monkeypatch):
    """게이트 적색 재현: 예산의 10배 가상 지연을 주입하면 예산 단언이 실제로 깨진다."""
    reset_virtual_totals()
    monkeypatch.setattr(
        _NHHTTPClient,
        "_request",
        virtual_delayed_async(_NHHTTPClient._request, BUDGET_MS * 10 / 1000),
    )

    sample = await PerfBudget().best_of_async(_one_retry_roundtrip, n=3, warmup=0)

    # 주입 지연(1000ms)이 측정에 반영돼 예산 단언(wall_ms < BUDGET_MS)이 실제로 적색이 된다.
    assert sample.wall_ms >= BUDGET_MS, "주입 지연이 측정에 반영되지 않으면 게이트가 무력하다"
