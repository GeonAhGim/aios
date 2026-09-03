"""적대적 통합테스트 — PLT-25(TRU-012): 폭주 상황에서도 정확히 `limit`개까지만
허용되고, 초과분은 라우터에 닿기 전에 429로 거절된다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-25

`tests/conftest.py`의 `_reset_rate_limiter_singleton`(autouse)이 기본값을
`UnlimitedRateLimiter`로 되돌려두므로, 이 파일의 각 테스트는 실제
`InMemoryTokenBucket`을 명시적으로 다시 꽂는다 — 다른 테스트 파일에 영향이
새지 않는다(그 자체가 이 리프의 DoD "conftest override로 기존 테스트 무영향").
"""
from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.contracts.error_codes import ErrorCode
from src.core.rate_limit.limiter import Decision, InMemoryTokenBucket, set_limiter
from src.core.rate_limit.policy import POLICIES
from src.main import app


@pytest.fixture
async def client():
    # 고정 시계 — `/openapi.json`은 프로세스 전체에서 최초 1회만 스키마를 만들고
    # 캐시하므로(FastAPI 내부) 이후 호출은 빠르지만, 이 파일이 그 최초 호출을
    # 트리거하는 첫 테스트가 되면 스키마 생성 자체가 초 단위로 걸릴 수 있다 —
    # 실시간 리필(rate=limit/window_seconds)이 그 지연을 토큰으로 되돌려주면
    # "정확히 limit개까지만 허용"이 실행 속도에 따라 흔들린다. 시계를 고정해
    # 리필을 0으로 만들면 폭주 시나리오(버스트 소진)만 결정론적으로 검증된다
    # — 시간 경과에 따른 리필 자체는 test_bucket_refills_after_window_elapses가
    # 별도로 검증한다.
    set_limiter(InMemoryTokenBucket(clock=lambda: 0.0))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_121st_read_request_is_rejected_with_429_envelope(client):
    limit = POLICIES["read"].limit

    for _ in range(limit):
        response = await client.get("/openapi.json")
        assert response.status_code == 200

    response = await client.get("/openapi.json")

    assert response.status_code == 429
    body = response.json()
    assert body["error_code"] == ErrorCode.RATE_LIMIT_EXCEEDED.value
    assert body["retry_after_seconds"] is not None and body["retry_after_seconds"] > 0
    assert response.headers["Retry-After"] == str(body["retry_after_seconds"])
    assert response.headers["RateLimit-Limit"] == str(limit)
    assert response.headers["RateLimit-Remaining"] == "0"
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Trace-Id"]


async def test_concurrent_storm_admits_exactly_limit_requests_no_partial_overrun(client):
    """동시에 `limit + 5`개를 던져도(gather — 순차가 아니라 동시 도착) 정확히
    `limit`개만 200이고 나머지 5개는 429다. 버킷 갱신이 락 없이 read-modify-write
    였다면 경합으로 `limit`개보다 더 많이 새어나갈 수 있었다(§9 PLT-25가
    막으려는 "105번 표준" 위반과 동일한 실패 유형) — 이 테스트가 그걸 막는다."""
    limit = POLICIES["read"].limit
    overflow = 5

    responses = await asyncio.gather(
        *[client.get("/openapi.json") for _ in range(limit + overflow)]
    )

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == limit
    assert statuses.count(429) == overflow


async def test_rate_limited_response_does_not_reach_route_handler(client):
    """미들웨어가 라우팅보다 먼저 거절하므로, 존재하지 않는 경로라도 한도
    소진 전에는 404(라우팅까지 도달), 소진 후에는 429(라우팅에 닿지 못함)다
    — "부분 변경 없음"이 라우팅 도달 여부로도 관찰 가능함을 보인다."""
    limit = POLICIES["mutation"].limit

    for _ in range(limit):
        response = await client.post("/no-such-mutation-route")
        assert response.status_code == 404

    response = await client.post("/no-such-mutation-route")

    assert response.status_code == 429


async def test_bucket_refills_after_window_elapses():
    """`InMemoryTokenBucket`은 시계를 주입받으므로(exchanges/common/rate_limiter.py
    와 동일 패턴) 실제 대기 없이 결정론적으로 리필을 검증한다 — 한도 소진 직후
    거절되고, window_seconds만큼 시간이 흐르면 다시 허용된다."""
    now = 1_000.0

    def clock() -> float:
        return now

    bucket = InMemoryTokenBucket(clock=clock)
    policy = POLICIES["mutation"]  # limit=10, window_seconds=60

    for _ in range(policy.limit):
        decision = await bucket.acquire(policy, "ip:1.2.3.4")
        assert decision.allowed

    denied = await bucket.acquire(policy, "ip:1.2.3.4")
    assert denied == Decision(allowed=False, retry_after_s=denied.retry_after_s, remaining=0)
    assert denied.retry_after_s is not None and denied.retry_after_s > 0

    now += policy.window_seconds
    recovered = await bucket.acquire(policy, "ip:1.2.3.4")
    assert recovered.allowed


async def test_distinct_keys_have_independent_buckets():
    """같은 정책이라도 키(IP)가 다르면 서로의 한도를 침범하지 않는다 — 한
    IP의 폭주가 다른 IP의 예산을 갉아먹으면 안 된다."""
    bucket = InMemoryTokenBucket(clock=time.monotonic)
    policy = POLICIES["mutation"]

    for _ in range(policy.limit):
        assert (await bucket.acquire(policy, "ip:1.1.1.1")).allowed

    assert not (await bucket.acquire(policy, "ip:1.1.1.1")).allowed
    assert (await bucket.acquire(policy, "ip:2.2.2.2")).allowed
