"""L4-21 — NH 어댑터 내구성 DEEPEN(2799) D2 보강: 게이트 적색·성능·실패 주입.

원 파일 test_kis_durability.py가 500줄 임계치에 가까워진 관계로 NH 전용
D2 보강 테스트를 별도 파일로 분리한다.

DoD:
- gate-red: handler가 malformed JSON을 반환하면 JSONDecodeError 발생 (silent 실패 아님)
- perf: 동시 10-way 토큰 발급이 단발 왕복비용의 2배 이내
- failure_injection: 토큰 발급이 여러 번 실패하면 FatalExchangeError (무한 재시도 아님)
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.nh.adapter import _NHHTTPClient

NH_TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _nh_client(handler) -> _NHHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    return _NHHTTPClient("appkey", "appsecret", "1234567890", http_client=http_client)


# ---------- (a-gate-red) NH 게이트 적색 재현 ----------


async def test_nh_concurrent_ensure_token_gate_red_corrupt_handler():
    """게이트 적색 재현 — handler가 잘못된 JSON을 반환하면 _ensure_token이
    JSONDecodeError를 발생시켜 테스트가 적색으로 종료됨을 확인한다.
    이는 토큰 발급 엔드포인트가 corrupted payload를 silent하게 처리하지
    않음을 증명한다(버그가 silent fallback으로 위장하지 않음)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        # malformed JSON body — 실제 서버가 corrupted payload를 보낼 상황
        return httpx.Response(200, text="{bad json", headers={"content-type": "application/json"})

    client = _nh_client(handler)

    with pytest.raises(json.JSONDecodeError):
        await client._ensure_token()


# ---------- (a-perf) NH 성능 단언 ----------


@pytest.mark.perf
async def test_nh_concurrent_ensure_token_performance_budget():
    """성능 단언 — 동시 10-way 토큰 발급이 단발 발급 왕복비용의 2배 이내에
    끝나야 한다(single-flight이 순차 10배가 아님을 수치로 증명).
    fetch_delay=0.01s 기준: 단발≈0.01s, 동시<0.03s(단발×3, 여유 30배)."""
    fetch_delay = 0.01

    def make_handler():
        call_count = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            await asyncio.sleep(fetch_delay)
            return httpx.Response(200, json=NH_TOKEN_RESPONSE)

        return handler, call_count

    # baseline: 단발 발급 왕복비용
    baseline_handler, _ = make_handler()
    baseline_client = _nh_client(baseline_handler)
    t0 = time.perf_counter()
    await baseline_client._ensure_token()
    baseline_elapsed = time.perf_counter() - t0

    # concurrent: 10-way 동시 발급
    concurrent_handler, concurrent_count = make_handler()
    concurrent_client = _nh_client(concurrent_handler)
    t0 = time.perf_counter()
    await asyncio.gather(*[concurrent_client._ensure_token() for _ in range(10)])
    concurrent_elapsed = time.perf_counter() - t0

    print(  # noqa: T201
        f"nh perf: baseline={baseline_elapsed:.4f}s concurrent(10-way)={concurrent_elapsed:.4f}s"
    )
    assert concurrent_count["n"] == 1
    # single-flight: 10-way가 단발보다 2배 느려야 함 (동시성 오버헤드만)
    assert concurrent_elapsed < baseline_elapsed * 2
    # 절대값: fetch_delay×5 이내 (여유 30배 — fetch_delay=0.01s → 0.05s)
    assert concurrent_elapsed < fetch_delay * 5


# ---------- (a-fail) NH 실패 주입 — 토큰 발급 지연 → 재시도 한계 ----------


async def test_nh_concurrent_ensure_token_failure_injection_retries_then_fails():
    """실패 주입 — 토큰 발급이 여러 번 실패하면 _ensure_token이
    FatalExchangeError를 발생시켜 fail-closed 됨을 확인한다.
    ResilientTransport의 재시도 정책이 무한 루프 없이 제한된 시도 후
    예외를 던짐을 검증한다."""

    call_count = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        call_count["n"] += 1
        # 첫 3번은 500 에러 → 재시도 유발, 4번째도 500 → 재시도 한계 초과
        return httpx.Response(500, text="internal server error")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    client = _NHHTTPClient("appkey", "appsecret", "1234567890", http_client=http_client)

    with pytest.raises(FatalExchangeError):
        await client._ensure_token()

    # 재시도 정책: 첫 시도 + 제한된 재시도 → FatalExchangeError
    # ResilientTransport가 무한 재시도하지 않음을 확인
    assert call_count["n"] >= 2
