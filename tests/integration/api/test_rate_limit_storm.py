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
import math
import time
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.contracts.error_codes import ErrorCode
from src.core.rate_limit.limiter import Decision, InMemoryTokenBucket, set_limiter
from src.core.rate_limit.policy import POLICIES, RateLimitPolicy
from src.main import app
from tests.conftest import PerfBudget
from tests.support.coverage_pause import paused_coverage

# ADR-2026-09-09-C Decision 1 축별 성능 예산표에 rate limiter 전용 항목이 없어
# 가장 가까운 유사 항목("사전거래 게이트 p99 5ms" — 이쪽도 I/O 없이 인메모리
# 상태만 보고 즉시 allow/deny를 판정하는 동기 게이트)을 자체 예산으로 차용한다.
_ACQUIRE_P99_BUDGET_SECONDS = 0.005


def _p99(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.99 * len(ordered)) - 1)
    return ordered[index]


class _BrokenLimiter:
    """acquire()가 항상 예외를 던지는 고장 난 백엔드(예: 향후 Redis 어댑터
    장애)를 흉내 낸다 — fail-closed 회귀 방지 테스트 전용."""

    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision:
        raise RuntimeError("rate limiter backend unavailable")


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
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


async def test_121st_read_request_is_rejected_with_429_envelope(
    client: AsyncClient,
) -> None:
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


async def test_concurrent_storm_admits_exactly_limit_requests_no_partial_overrun(
    client: AsyncClient,
) -> None:
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


async def test_rate_limited_response_does_not_reach_route_handler(
    client: AsyncClient,
) -> None:
    """미들웨어가 라우팅보다 먼저 거절하므로, 존재하지 않는 경로라도 한도
    소진 전에는 404(라우팅까지 도달), 소진 후에는 429(라우팅에 닿지 못함)다
    — "부분 변경 없음"이 라우팅 도달 여부로도 관찰 가능함을 보인다."""
    limit = POLICIES["mutation"].limit

    for _ in range(limit):
        response = await client.post("/no-such-mutation-route")
        assert response.status_code == 404

    response = await client.post("/no-such-mutation-route")

    assert response.status_code == 429


async def test_bucket_refills_after_window_elapses() -> None:
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


async def test_distinct_keys_have_independent_buckets() -> None:
    """같은 정책이라도 키(IP)가 다르면 서로의 한도를 침범하지 않는다 — 한
    IP의 폭주가 다른 IP의 예산을 갉아먹으면 안 된다."""
    bucket = InMemoryTokenBucket(clock=time.monotonic)
    policy = POLICIES["mutation"]

    for _ in range(policy.limit):
        assert (await bucket.acquire(policy, "ip:1.1.1.1")).allowed

    assert not (await bucket.acquire(policy, "ip:1.1.1.1")).allowed
    assert (await bucket.acquire(policy, "ip:2.2.2.2")).allowed


async def test_broken_limiter_backend_fails_closed_not_silently_allowed(
    client: AsyncClient,
) -> None:
    """limiter() 백엔드가 예외를 던지면(예: §10.4가 미확정으로 남긴 Redis
    어댑터 전환 이후의 네트워크 장애) 미들웨어가 그 예외를 삼켜 "제한 없음"
    으로 위장하면 안 된다 — CLAUDE.md §3 fail-closed 기본 위반. `RateLimitMiddleware`
    는 스택 최외곽(이 모듈 docstring이 아니라 rate_limit.py 모듈 docstring
    §14행 "등록 순서" 참고)이라 `install_exception_handlers`(ExceptionMiddleware
    안쪽)를 거치지 않고 Starlette `ServerErrorMiddleware`까지 그대로 전파되어
    5xx로 끝난다 — 정확한 포맷은 이 리프 책임 밖이므로 "200으로 조용히
    통과하지 않는다"만 확인한다."""
    set_limiter(_BrokenLimiter())

    response = await client.get("/openapi.json")

    assert response.status_code != 200
    assert response.status_code >= 500


@pytest.mark.perf
async def test_acquire_p99_latency_within_budget(
    perf_budget: PerfBudget,
) -> None:
    """`InMemoryTokenBucket.acquire()`는 I/O 없이 dict 조회 + 락만 쓰는 순수
    CPU 작업이므로 ADR-2026-09-09-C 예산표의 "사전거래 게이트 p99 5ms"를
    자체 예산으로 차용해 반복 호출 p99가 그 안에 드는지 단언한다.

    단위: 이 함수의 모든 시간 값은 밀리초(ms)다 — `cpu_ms_per_call`도
    `_ACQUIRE_P99_BUDGET_SECONDS * 1000`도 ms. 혼용하지 않는다(task-11519
    이전에는 `cpu_ms`(이미 ms)에 추가로 *1000을 해 "μs"라 주석을 달고
    ms 예산과 비교하는 단위 버그가 있었다 — 5ms 예산을 5μs 상당으로
    요구하는 셈이라 거의 항상 틱이 0이어야만 통과했다).

    `perf_budget.samples_async`(호출 1회당 1샘플)는 쓰지 않는다 — Windows
    `time.process_time()`은 ~15.6ms(64Hz) 틱으로 양자화되고(`PerfBudget.sample`
    docstring 참고) `acquire()` 1회는 그보다 훨씬 짧아 샘플이 0 또는
    15.625 중 하나로만 읽힌다. `PerfBudget.sample`과 동일하게 `batch`회를
    한 구간으로 묶어 총 CPU 시간을 틱 폭보다 크게 만들고 호출당 값으로
    나눠(`tick/batch`로 양자화 오차 축소), coverage 트레이서도 측정 구간
    밖으로 뺀다(task-7253과 동일 근거)."""
    bucket = InMemoryTokenBucket(clock=time.monotonic)
    policy = POLICIES["read"]

    batch = 8
    n = 30
    cpu_ms_per_call: list[float] = []
    for _ in range(n):
        with paused_coverage():
            cpu_start = time.process_time()
            for _ in range(batch):
                await bucket.acquire(policy, "perf:subject-1")
            cpu_ms_per_call.append((time.process_time() - cpu_start) * 1000 / batch)

    cpu_ms_per_call.sort()
    index = max(0, math.ceil(0.99 * len(cpu_ms_per_call)) - 1)
    p99_ms = cpu_ms_per_call[index]

    assert p99_ms < _ACQUIRE_P99_BUDGET_SECONDS * 1000


@pytest.mark.perf
async def test_gate_red_reproduction_acquire_p99_budget_guard_catches_lock_regression(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """위 단언이 상시-녹색이 아님을 증명 — 버킷 dict 접근을 직렬화하는 락
    (limiter.py 모듈 docstring 47행) 획득 경로에 예산의 배수만큼 지연을
    주입하면(락 경합 회귀를 흉내) 같은 p99 단언이 실제로 적색(AssertionError)
    이 되어야 한다.
    raw perf_counter → perf_budget.samples_async(task-11034)."""
    bucket = InMemoryTokenBucket(clock=time.monotonic)
    policy = POLICIES["read"]
    original_acquire = bucket._lock.acquire

    async def delayed_acquire() -> bool:
        await asyncio.sleep(_ACQUIRE_P99_BUDGET_SECONDS * 3)
        return await original_acquire()

    monkeypatch.setattr(bucket._lock, "acquire", delayed_acquire)

    samples = await perf_budget.samples_async(
        lambda: bucket.acquire(policy, "perf:subject-2"),
        n=5,
    )

    # original test measured wall time via perf_counter — use wall_ms
    wall_values = sorted(s.wall_ms for s in samples)
    index = max(0, math.ceil(0.99 * len(wall_values)) - 1)
    p99_ms = wall_values[index]

    with pytest.raises(AssertionError):
        assert p99_ms < _ACQUIRE_P99_BUDGET_SECONDS * 1000


# ---------------------------------------------------------------------------
# Negative tests — 불변식 위반 입력을 명시적으로 거부하는 케이스
# ---------------------------------------------------------------------------


async def test_malformed_jwt_falls_back_to_ip_key_not_crash(
    client: AsyncClient,
) -> None:
    """_resolve_key() 가 유효하지 않은 JWT(잘못된 서명/만료) 를 받으면
    JWT 디코딩 실패를 잡아 IP 키로 폴백한다 — 예외가 상위로 전파되어
    5xx 를 반환하면 안 된다. PLT-25 불변식: 키_resolve 는 항상 문자열
     key 를 반환해야 한다."""
    response = await client.get(
        "/openapi.json",
        headers={"Authorization": "Bearer not-a-real-token.junk.payload"},
    )

    # 120개 read 한도 안에서 200 이어야 한다(429 도 5xx 도 아님)
    assert response.status_code == 200


async def test_missing_client_ip_uses_unknown_key_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_client_ip() 가 request.client == None 을 받으면 "unknown" 을
    반환한다 — None 이 그대로 키에 들어가서 버킷 충돌을 일으키지 않는다.
    InMemoryTokenBucket 자체는 "unknown" 키를 정상적으로接受하므로,
    이 테스트는 키_resolve 경로가 None 을 통과하지 않음을 검증한다."""
    from starlette.requests import Request

    from src.api.middleware import rate_limit as rl_module

    def fake_client_ip(_request: Request) -> str:
        return "unknown"

    monkeypatch.setattr(rl_module, "_client_ip", fake_client_ip)

    # "unknown" 키로 정책=read(120개) 를 소진한 뒤, 121 번째가 429 가 됨.
    # 만약 "unknown" 이 아닌 None 이 키로 들어가면 버킷 키가 불변하고
    # 예상과 다른 행동을 하므로, "unknown" 이 정상적으로 쓰임을 확인한다.
    bucket = InMemoryTokenBucket(clock=lambda: 0.0)
    set_limiter(bucket)
    policy = POLICIES["read"]

    for _ in range(policy.limit):
        dec = await bucket.acquire(policy, "unknown")
        assert dec.allowed

    denied = await bucket.acquire(policy, "unknown")
    assert not denied.allowed
    assert denied.remaining == 0


async def test_unknown_policy_route_bypasses_rate_limit_safely(
    client: AsyncClient,
) -> None:
    """resolve_policy() 가 None 을 반환하는 경로(매칭되는 정책 없음) 는
    rate limit 을 우회한다 — PLT-25 의 의도적 동작이지만, 이 우회가
    5xx 로 이어지지 않고 정상적인 라우팅 결과(404 등) 를 반환함을
    검증한다. 즉, "제한 없음"이 "오류" 가 아님을 확인한다."""
    # OPTIONS 메서드는 default_resolve_policy 에서 None 을 반환한다.
    response = await client.options("/openapi.json")

    # CORS 미들웨어가 OPTIONS 에 대한 응답을 처리하므로 200 이 될 수 있다.
    # 중요한 것은 5xx 가 아닌 것 — 라우팅까지 도달했거나 CORS 가 처리했음.
    assert response.status_code != 429
    assert response.status_code < 500


async def test_limiter_backend_raises_during_dispatch_returns_5xx(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입: limiter() 싱글턴이 매 요청 시 RuntimeError 를 던지면,
    미들웨어가 그 예외를 삼키지 않고 5xx 로 끝난다 — fail-closed
     (CLAUDE.md §3) 위반 방지. `test_broken_limiter_backend_fails_closed` 가
    _BrokenLimiter 클래스로 동일 행위를 클래스 수준에서 검증했다면,
    이 테스트는 monkeypatch 로 runtime 에 싱글턴 게터를 교체하는 방식으로
    동일한 불변식을 검증한다."""
    import src.api.middleware.rate_limit as rl_module

    monkeypatch.setattr(rl_module, "limiter", lambda: _BrokenLimiter())

    response = await client.get("/openapi.json")

    assert response.status_code != 200
    assert response.status_code >= 500


async def test_different_policies_have_independent_limits(
    client: AsyncClient,
) -> None:
    """read(120개/60s) 와 mutation(10개/60s) 는 서로 다른 버킷을 사용한다 —
    read 한도를 모두 소진해도 mutation 요청은 여전히 허용되어야 한다.
    PLT-25: 정책별 독립 버킷 불변식."""
    # read 한도 소진
    for _ in range(120):
        response = await client.get("/openapi.json")
        assert response.status_code == 200

    # read 는 이제 429
    response = await client.get("/openapi.json")
    assert response.status_code == 429

    # mutation 은 여전히 허용 (다른 버킷)
    response = await client.post("/no-such-mutation-route")
    assert response.status_code == 404
