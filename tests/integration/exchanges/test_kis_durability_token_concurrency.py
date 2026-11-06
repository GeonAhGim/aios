"""L4-21 — KIS/NH 토큰 발급 성능과 다중 인스턴스 격리 회귀 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-21.
DEEPEN(task-2799)의 성능·격리 단언을 책임별로 분리한다(task-23017).
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from src.exchanges.kis.adapter import _KISHTTPClient
from src.exchanges.nh.adapter import _NHHTTPClient
from tests.integration.exchanges.test_kis_durability import (
    KIS_TOKEN_RESPONSE,
    NH_TOKEN_RESPONSE,
    _kis_client,
    _nh_client,
)

# ---------- (f) DEEPEN(2799) — 수치 성능 + 다중 인스턴스 + adversarial ----------


@pytest.mark.perf
async def test_kis_concurrent_token_issuance_stays_near_single_fetch_latency():
    """수치 지연 단언 — 절대 ms 상수는 쓰지 않는다(공유 CI 환경에서 절대
    임계가 로컬 대비 크게 변동한 전례, test_gate_perf_multiinstance.py와
    동일 근거). 대신 같은 handler 지연(fetch_delay)의 단발 발급 왕복비용에
    정규화한다. 순차 재발급이었다면 10 * fetch_delay만큼 걸렸을 동시
    10-way 발급이, 실제로는 단발 발급과 비슷한 시간에 끝남을 증명한다 —
    (a) 테스트는 호출 횟수만 셌지 실제로 "빨라졌는지"는 재지 않았다."""
    fetch_delay = 0.05

    def make_handler():
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(fetch_delay)
            return httpx.Response(200, json=KIS_TOKEN_RESPONSE)

        return handler, call_count

    baseline_handler, baseline_count = make_handler()
    baseline_client = _kis_client(baseline_handler)
    t0 = time.perf_counter()
    await baseline_client._ensure_token()
    baseline_elapsed = time.perf_counter() - t0

    concurrent_handler, concurrent_count = make_handler()
    concurrent_client = _kis_client(concurrent_handler)
    t0 = time.perf_counter()
    await asyncio.gather(*[concurrent_client._ensure_token() for _ in range(10)])
    concurrent_elapsed = time.perf_counter() - t0

    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"kis single-flight token issuance: baseline={baseline_elapsed:.4f}s "
        f"concurrent(10-way)={concurrent_elapsed:.4f}s"
    )
    assert concurrent_count["n"] == 1
    # 순차 재발급이었다면 ~10 * fetch_delay(0.5s)가 걸렸을 것 — 그 절반에도
    # 못 미치는, 단발 발급 대비 넉넉한 배수 안에서 끝나야 한다.
    assert concurrent_elapsed < baseline_elapsed * 5
    assert concurrent_elapsed < fetch_delay * 5


@pytest.mark.perf
async def test_nh_concurrent_token_issuance_stays_near_single_fetch_latency():
    fetch_delay = 0.05

    def make_handler():
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(fetch_delay)
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)

        return handler, call_count

    baseline_handler, baseline_count = make_handler()
    baseline_client = _nh_client(baseline_handler)
    t0 = time.perf_counter()
    await baseline_client._ensure_token()
    baseline_elapsed = time.perf_counter() - t0

    concurrent_handler, concurrent_count = make_handler()
    concurrent_client = _nh_client(concurrent_handler)
    t0 = time.perf_counter()
    await asyncio.gather(*[concurrent_client._ensure_token() for _ in range(10)])
    concurrent_elapsed = time.perf_counter() - t0

    print(  # noqa: T201
        f"nh single-flight token issuance: baseline={baseline_elapsed:.4f}s "
        f"concurrent(10-way)={concurrent_elapsed:.4f}s"
    )
    assert concurrent_count["n"] == 1
    assert concurrent_elapsed < baseline_elapsed * 5
    assert concurrent_elapsed < fetch_delay * 5


async def test_kis_two_independent_client_instances_isolate_token_issuance():
    """다중 인스턴스 증명 — 기존 (a) 테스트는 인스턴스 하나 안에서의
    동시성만 봤다. 여기서는 서로 다른 두 `_KISHTTPClient` 인스턴스(서로
    다른 워커 프로세스가 각자 소유하는 상황과 동등)가 정확히 같은
    시각에(asyncio.gather로 중첩) 각자 10-way 동시 토큰 요청을 걸어도,
    `MonotonicTokenCache`/`asyncio.Lock`이 인스턴스별로 격리되어 있어
    상태가 서로 새지 않고 각 인스턴스가 독립적으로 정확히 1회만 발급함을
    증명한다."""

    def make_handler(token_value: str) -> tuple:
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(
                200,
                json={
                    "access_token": token_value,
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )

        return handler, call_count

    handler_a, count_a = make_handler("tok-a")
    handler_b, count_b = make_handler("tok-b")
    client_a = _kis_client(handler_a)
    client_b = _kis_client(handler_b)

    async def run_instance(client: _KISHTTPClient) -> list:
        return await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    tokens_a, tokens_b = await asyncio.gather(run_instance(client_a), run_instance(client_b))

    assert count_a["n"] == 1
    assert count_b["n"] == 1
    assert all(t == "tok-a" for t in tokens_a)  # 인스턴스 A는 자기 토큰만 본다
    assert all(t == "tok-b" for t in tokens_b)  # 인스턴스 B는 자기 토큰만 본다(교차 오염 없음)


async def test_nh_two_independent_client_instances_isolate_token_issuance():
    def make_handler(token_value: str) -> tuple:
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": token_value, "expires_in": 86400})

        return handler, call_count

    handler_a, count_a = make_handler("tok-a")
    handler_b, count_b = make_handler("tok-b")
    client_a = _nh_client(handler_a)
    client_b = _nh_client(handler_b)

    async def run_instance(client: _NHHTTPClient) -> list:
        return await asyncio.gather(*[client._ensure_token() for _ in range(10)])

    tokens_a, tokens_b = await asyncio.gather(run_instance(client_a), run_instance(client_b))

    assert count_a["n"] == 1
    assert count_b["n"] == 1
    assert all(t == "tok-a" for t in tokens_a)
    assert all(t == "tok-b" for t in tokens_b)
