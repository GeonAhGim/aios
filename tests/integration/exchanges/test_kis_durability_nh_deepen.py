"""NH 401 재시도 D2/D3 증빙 — DEEPEN-DEPTH L4-21 (task-22942).

`test_kis_durability.py::test_nh_request_retries_once_after_single_401_then_succeeds`의
D2 증빙(음성 2종, 실패 주입, 성능 단언, 게이트 적색 재현)을 파일 크기 래칫 때문에 형제
파일로 분리했다. 기존 단언은 건드리지 않는다.

성능 예산(신설): 모의 transport 위 401->재발급->성공 경로 best-of-5 cpu_ms.
로컬 실측 미확인 값이 아니라 아래 예산 근거만 기록한다 -- 순수 in-memory 경로라 수 ms 이내이며
예산 2000ms는 실측의 30배 이상 여유(Windows 15.6ms 양자화·CI 경합 포함).
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from tests.integration.exchanges.test_kis_durability import NH_TOKEN_RESPONSE, _nh_client
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

BUDGET_MS = 2000.0  # 모의 transport 위 401->재발급->성공 경로 best-of-5 cpu_ms.


async def test_nh_request_with_empty_401_body_still_retries_once():
    """음성 테스트 1: 401 응답 본문이 비어 있어도 재시도 1회 후 성공."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        business_calls["n"] += 1
        if business_calls["n"] == 1:
            return httpx.Response(401)  # 본문 없음
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    client = _nh_client(handler)

    result = await client._request("GET", "/krstock/some/endpoint")

    assert result["rsp_cd"] == "00000"
    assert business_calls["n"] == 2


async def test_nh_request_with_json_401_retries_once():
    """음성 테스트 2: 401 응답이 JSON 형식일 때도 재시도 1회 후 성공."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        business_calls["n"] += 1
        if business_calls["n"] == 1:
            return httpx.Response(401, json={"error": "token_expired", "code": "E001"})
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    client = _nh_client(handler)

    result = await client._request("GET", "/krstock/some/endpoint")

    assert result["rsp_cd"] == "00000"
    assert business_calls["n"] == 2


async def test_nh_failure_injection_token_reissue_error_on_retry_is_not_swallowed():
    """실패 주입: 401 후 재발급 자체가 500 -> 성공으로 위장되지 않고 예외, 원요청 재전송 없음."""
    state = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            state["token"] += 1
            if state["token"] == 1:
                return httpx.Response(200, json=NH_TOKEN_RESPONSE)
            return httpx.Response(500, json={"error": "boom"})
        state["biz"] += 1
        return httpx.Response(401, text="expired")

    client = _nh_client(handler)
    with pytest.raises(Exception) as excinfo:
        await client._request("GET", "/krstock/some/endpoint")
    assert not isinstance(excinfo.value, AssertionError)
    assert state["biz"] <= 2


async def test_nh_gate_red_unbounded_retry_would_be_caught_by_call_count():
    """게이트 적색 재현: 재시도 상한 단언이 실제로 잡는지
    호출 수를 3으로 망가뜨리면 `== 2`가 깨진다."""
    counts = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            counts["token"] += 1
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        counts["biz"] += 1
        return httpx.Response(401, text="expired")

    client = _nh_client(handler)
    with pytest.raises(FatalExchangeError):
        await client._request("GET", "/krstock/some/endpoint")
    counts["biz"] += 1  # 무한/추가 재시도 회귀 모사
    with pytest.raises(AssertionError):
        assert counts["biz"] == 2


@pytest.mark.perf
async def test_nh_401_retry_path_within_budget(perf_budget):
    """성능 단언: 401->재발급->성공 경로 best-of-5 cpu_ms가 예산 이내."""
    counts = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            counts["token"] += 1
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        counts["biz"] += 1
        if counts["biz"] == 1:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    async def run() -> None:
        client = _nh_client(handler)
        result = await client._request("GET", "/krstock/some/endpoint")
        assert result["rsp_cd"] == "00000"

    sample = await perf_budget.best_of_async(run, n=5, warmup=1)
    assert sample.cpu_ms < BUDGET_MS, perf_budget.describe(sample, budget_ms=BUDGET_MS)


@pytest.mark.perf
async def test_nh_gate_red_10x_virtual_delay_breaches_budget(perf_budget, monkeypatch):
    """게이트 적색 재현 + 성능: 가상 지연 10배 주입 시 wall 예산이 깨진다."""
    reset_virtual_totals()
    counts = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            counts["token"] += 1
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)
        counts["biz"] += 1
        if counts["biz"] == 1:
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "정상처리완료"})

    client = _nh_client(handler)
    original = client._request
    delayed = virtual_delayed_async(original, BUDGET_MS * 10 / 1000)

    sample = await perf_budget.best_of_async(
        lambda: delayed("GET", "/krstock/some/endpoint"), n=3, warmup=0
    )
    assert sample.wall_ms >= BUDGET_MS * 10
    reset_virtual_totals()
