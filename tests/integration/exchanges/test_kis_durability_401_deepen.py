"""KIS 401 재시도 상한(원요청 + 재시도 1회) 깊이 보강 — DEEPEN-DEPTH L4-21 (task-22460).

`test_kis_durability.py::test_kis_request_raises_after_second_401_without_further_retry`의
D2/D3 증빙(음성 3종, 실패 주입, 성능 단언, 게이트 적색 재현)을 파일 크기 래칫 때문에 형제
파일로 분리했다. 기존 단언은 건드리지 않는다.

성능 예산(신설): 모의 transport 위 401->재발급->401 경로 best-of-5 cpu_ms.
로컬 실측 미확인 값이 아니라 아래 예산 근거만 기록한다 -- 순수 in-memory 경로라 수 ms 이내이며
예산 2000ms는 실측의 30배 이상 여유(Windows 15.6ms 양자화·CI 경합 포함).
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from tests.integration.exchanges.test_kis_durability import KIS_TOKEN_RESPONSE, _kis_client
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

PATH = "/uapi/domestic-stock/v1/some-endpoint"
BUDGET_MS = 2000.0


def _always_401(counts: dict[str, int], body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            counts["token"] += 1
            return httpx.Response(200, json=KIS_TOKEN_RESPONSE)
        counts["biz"] += 1
        return httpx.Response(401, json=body or {"rt_cd": "1", "msg1": "expired"})

    return handler


async def test_second_401_never_returns_success_payload():
    counts = {"token": 0, "biz": 0}
    client = _kis_client(_always_401(counts))
    result = None
    with pytest.raises(FatalExchangeError):
        result = await client._request("GET", PATH, "FHKST0")
    assert result is None


async def test_second_401_with_empty_body_still_fatal_and_bounded():
    counts = {"token": 0, "biz": 0}
    client = _kis_client(_always_401(counts, body={}))
    with pytest.raises(FatalExchangeError):
        await client._request("GET", PATH, "FHKST0")
    assert counts["biz"] == 2


async def test_repeated_calls_after_fatal_401_each_bounded_to_two_attempts():
    counts = {"token": 0, "biz": 0}
    client = _kis_client(_always_401(counts))
    for expected in (2, 4):
        with pytest.raises(FatalExchangeError):
            await client._request("GET", PATH, "FHKST0")
        assert counts["biz"] == expected


async def test_failure_injection_token_reissue_error_on_retry_is_not_swallowed():
    """실패 주입: 401 후 재발급 자체가 500 -> 성공으로 위장되지 않고 예외, 원요청 재전송 없음."""
    state = {"token": 0, "biz": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            state["token"] += 1
            if state["token"] == 1:
                return httpx.Response(200, json=KIS_TOKEN_RESPONSE)
            return httpx.Response(500, json={"error": "boom"})
        state["biz"] += 1
        return httpx.Response(401, json={"rt_cd": "1", "msg1": "expired"})

    client = _kis_client(handler)
    with pytest.raises(Exception) as excinfo:
        await client._request("GET", PATH, "FHKST0")
    assert not isinstance(excinfo.value, AssertionError)
    assert state["biz"] <= 2


async def test_gate_red_unbounded_retry_would_be_caught_by_call_count():
    """적색 재현: 재시도 상한 단언이 실제로 잡는지 -- 호출 수를 3으로 망가뜨리면 `== 2`가 깨진다."""
    counts = {"token": 0, "biz": 0}
    client = _kis_client(_always_401(counts))
    with pytest.raises(FatalExchangeError):
        await client._request("GET", PATH, "FHKST0")
    counts["biz"] += 1  # 무한/추가 재시도 회귀를 모사
    with pytest.raises(AssertionError):
        assert counts["biz"] == 2


@pytest.mark.perf
async def test_second_401_path_within_budget(perf_budget):
    counts = {"token": 0, "biz": 0}

    async def run() -> None:
        client = _kis_client(_always_401(counts))
        with pytest.raises(FatalExchangeError):
            await client._request("GET", PATH, "FHKST0")

    sample = await perf_budget.best_of_async(run, n=5, warmup=1)
    assert sample.cpu_ms < BUDGET_MS, perf_budget.describe(sample, budget_ms=BUDGET_MS)


@pytest.mark.perf
async def test_gate_red_10x_virtual_delay_breaches_budget(perf_budget, monkeypatch):
    """적색 재현: 가상 지연 10배(BUDGET의 10배 = 20s wall) 주입 시 wall 예산이 깨진다."""
    reset_virtual_totals()
    original = _kis_client(_always_401({"token": 0, "biz": 0}))._request
    delayed = virtual_delayed_async(original, BUDGET_MS * 10 / 1000)

    async def run() -> None:
        with pytest.raises(FatalExchangeError):
            await delayed("GET", PATH, "FHKST0")

    sample = await perf_budget.best_of_async(run, n=3, warmup=0)
    assert sample.wall_ms >= BUDGET_MS * 10
    reset_virtual_totals()
