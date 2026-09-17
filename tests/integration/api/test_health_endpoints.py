"""통합테스트 — PLT-09: `/readyz`·`/livez`·`/metrics` 엔드포인트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.2, §9 PLT-09.

DoD(task-939): 정상 시 `GET /readyz` 200, DB 풀을 끊으면 503, `/metrics`는
토큰 없이 403. `/readyz`·`/livez`는 `ApiResponse` 봉투를 쓰지 않으므로
`response.json()`이 바로 `ReadinessReport` 모양이어야 한다(frontend
readiness.ts의 raw-first 파싱 분기와 대응).

task-3158 DEEPEN: 원 리프(task-939)는 negative·실패 주입은 이미 충족하지만
수치 성능 단언이 없었다(`_check_loops()`의 `observed < threshold` 정적
비교는 판정 로직이지 지연 측정이 아니다) — 게이트 적색 재현도 없었다. 새
기능 추가 없이 `/readyz`가 스펙 521행 "read 라우트 p95 < 300 ms"(73 §10)
예산을 지키는지, 그 단언이 실제 회귀에는 적색이 되는지(상시-녹색 아님)를
보강한다.
"""

from __future__ import annotations

import asyncio
import math
import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.core.observability.loop_health import LoopHealth, loop_health, set_loop_health
from src.main import app
from tests.conftest import lifespan_context_with_retry

_READYZ_READ_ROUTE_P95_BUDGET_SECONDS = 0.3


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    app.dependency_overrides.pop(get_pool, None)


class _BrokenPool:
    """`get_pool` 오버라이드용 더블 — 실제 asyncpg.Pool 대신 항상 연결
    실패를 흉내낸다(DB 풀을 실제로 끊지 않고도 §9 PLT-09 negative case를
    재현하기 위함)."""

    async def fetchval(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("pool closed: connection to server was lost")


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def test_readyz_returns_200_when_healthy(client: AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db_pool"]["ok"] is True


async def test_readyz_returns_503_when_db_pool_broken(client: AsyncClient) -> None:
    async def _broken_pool() -> _BrokenPool:
        return _BrokenPool()

    app.dependency_overrides[get_pool] = _broken_pool

    response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db_pool"]["ok"] is False
    # PLT-02 레닥션 — 원본 예외 문구(연결 정보 등)가 응답에 그대로 새면 안 된다.
    assert "pool closed" not in body["checks"]["db_pool"]["detail"]


async def test_readyz_returns_503_when_loop_last_success_is_stale(client: AsyncClient) -> None:
    clock = _FakeClock()
    fake_health = LoopHealth(clock=clock)
    fake_health.record_tick("heartbeat", True, 0.01, interval_sec=10.0)
    clock.now += 10.0 * 3 + 1.0  # 3×interval 임계값을 넘겨 stale로 만든다

    previous = loop_health()
    set_loop_health(fake_health)
    try:
        response = await client.get("/readyz")
    finally:
        set_loop_health(previous)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["loop:heartbeat"]["ok"] is False
    assert body["checks"]["loop:heartbeat"]["threshold"] == 30.0


async def test_livez_returns_200_without_touching_db(client: AsyncClient) -> None:
    async def _explode() -> None:
        raise AssertionError("livez는 DB pool 의존성을 절대 거치면 안 된다")

    app.dependency_overrides[get_pool] = _explode

    response = await client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_metrics_without_token_returns_403(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIOS_METRICS_TOKEN", raising=False)

    response = await client.get("/metrics")

    assert response.status_code == 403


async def test_metrics_with_wrong_token_returns_403(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIOS_METRICS_TOKEN", "correct-token")

    response = await client.get("/metrics", headers={"X-Metrics-Token": "wrong-token"})

    assert response.status_code == 403


async def test_metrics_with_correct_token_returns_prometheus_text(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIOS_METRICS_TOKEN", "correct-token")

    response = await client.get("/metrics", headers={"X-Metrics-Token": "correct-token"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


# ---------------------------------------------------------------------------
# task-3158 DEEPEN — `/readyz` 지연 예산(스펙 521행 "read 라우트 p95 <
# 300 ms")의 수치 성능 단언 + 게이트 적색 재현.
#
# `_check_loops()`의 `observed < threshold` 비교는 판정 로직(loop 신선도)이지
# 지연 측정이 아니다 — 이 두 테스트는 엔드포인트 왕복 자체의 p95 지연을
# 실측하고, 그 단언이 실제 회귀에는 적색이 되는지(상시-녹색 아님)까지
# 증명한다.
# ---------------------------------------------------------------------------


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


class _SlowPool:
    """`get_pool` 오버라이드용 더블 — `fetchval`에 read 라우트 예산의 2배
    지연을 인위 주입해, 아래 p95 단언이 tautology가 아니라 실제 회귀를
    잡는지(게이트 적색 재현) 증명한다."""

    async def fetchval(self, *args: object, **kwargs: object) -> int:
        await asyncio.sleep(_READYZ_READ_ROUTE_P95_BUDGET_SECONDS * 2)
        return 1


async def test_readyz_p95_latency_within_read_route_budget(client: AsyncClient) -> None:
    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        response = await client.get("/readyz")
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200

    assert _p95(samples) < _READYZ_READ_ROUTE_P95_BUDGET_SECONDS


async def test_readyz_p95_latency_budget_fails_when_db_pool_is_slow(
    client: AsyncClient,
) -> None:
    async def _slow_pool() -> _SlowPool:
        return _SlowPool()

    app.dependency_overrides[get_pool] = _slow_pool

    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        response = await client.get("/readyz")
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200

    with pytest.raises(AssertionError):
        assert _p95(samples) < _READYZ_READ_ROUTE_P95_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# task-4131 DEEPEN — 추가 negative/불변식 위반 케이스.
#
# 원 리프(task-939)와 task-3158 DEEPEN이 coverage를 넓혔으나 다음
# 불변식 위반 케이스가 누락되어 있었다:
#   I-07(응답 구조): readiness/liveness 엔드포인트는 ApiResponse 봉투를
#     쓰지 않아야 한다(운영 프로브는 빠르고 단순해야 함).
#   I-09(오류 축약): db_pool check.detail에 원본 예외 메시지(DSN/호스트)
#    가 새어 나오면 안 된다(PLT-02).
#   INVARIANTS.md §loop_freshness: interval 미설정 루프는 ready로 판정.
# ---------------------------------------------------------------------------


async def test_readyz_does_not_wrap_in_api_response(client: AsyncClient) -> None:
    """I-07: readiness 엔드포인트는 ApiResponse 봉투를 쓰지 않는다.

    운영 프로브는 `response.json()`이 바로 `ReadinessReport` 모양이어야
    하므로, `data`·`error` 같은 봉투 필드가 있으면 안 된다.
    """
    response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    # ApiResponse 봉투 필드가 없어야 함.
    assert "data" not in body
    assert "error" not in body
    # 대신 ReadinessReport 직시 필드가 있어야 함.
    assert "status" in body
    assert "checks" in body
    assert "as_of" in body


async def test_livez_does_not_wrap_in_api_response(client: AsyncClient) -> None:
    """I-07: liveness 엔드포인트도 ApiResponse 봉투를 쓰지 않는다."""
    response = await client.get("/livez")

    assert response.status_code == 200
    body = response.json()
    assert "data" not in body
    assert "error" not in body
    assert "status" in body


async def test_readyz_db_check_detail_does_not_leak_connection_info(
    client: AsyncClient,
) -> None:
    """I-09/PLT-02: db_pool check.detail에 DSN·호스트·원본 예외가 새면 안 된다."""

    async def _broken_pool() -> _BrokenPool:
        return _BrokenPool()

    app.dependency_overrides[get_pool] = _broken_pool

    response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    detail = body["checks"]["db_pool"]["detail"]
    # DSN 관련 키워드가 detail에 섞이지 않아야 함.
    for keyword in ("user", "password", "localhost", "5432", "asyncpg", "dsn"):
        assert keyword not in detail.lower(), f"detail에 민감 정보 누출: '{keyword}'"


async def test_readyz_with_no_loop_records_returns_200(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INVARIANTS.md loop_freshness: 아직 tick 한 번도 안 한 루프는 ready에 영향 없다.

    `loop_health().snapshot()`이 빈 딕셔너리를 반환하면 `_check_loops()`는
    loop check를 생성하지 않고, db_pool만 남으므로 DB가 정상이면 ready.
    """
    from src.core.observability.loop_health import LoopHealth

    empty_health = LoopHealth(clock=_FakeClock())
    # record_tick() 한 번도 호출하지 않음 → snapshot() == {}

    previous = loop_health()
    set_loop_health(empty_health)
    try:
        response = await client.get("/readyz")
    finally:
        set_loop_health(previous)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    # loop 관련 check가 하나도 없어야 함.
    loop_keys = [k for k in body["checks"] if k.startswith("loop:")]
    assert loop_keys == []


async def test_readyz_returns_both_db_and_loop_failures_in_checks(
    client: AsyncClient,
) -> None:
    """불변식 위반: 여러 check가 동시에 실패해도 status는 'not_ready'여야 하고
    각 check의 ok 필드가 개별 결과를 반영해야 한다."""
    clock = _FakeClock()
    fake_health = LoopHealth(clock=clock)
    fake_health.record_tick("heartbeat", True, 0.01, interval_sec=10.0)
    clock.now += 10.0 * 3 + 1.0  # stale

    async def _broken_pool() -> _BrokenPool:
        return _BrokenPool()

    previous = loop_health()
    set_loop_health(fake_health)
    app.dependency_overrides[get_pool] = _broken_pool
    try:
        response = await client.get("/readyz")
    finally:
        set_loop_health(previous)
        app.dependency_overrides.pop(get_pool, None)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    # 두 check가 모두 실패로 표시되어야 함.
    assert body["checks"]["db_pool"]["ok"] is False
    assert body["checks"]["loop:heartbeat"]["ok"] is False
