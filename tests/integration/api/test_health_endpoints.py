"""통합테스트 — PLT-09: `/readyz`·`/livez`·`/metrics` 엔드포인트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.2, §9 PLT-09.

DoD(task-939): 정상 시 `GET /readyz` 200, DB 풀을 끊으면 503, `/metrics`는
토큰 없이 403. `/readyz`·`/livez`는 `ApiResponse` 봉투를 쓰지 않으므로
`response.json()`이 바로 `ReadinessReport` 모양이어야 한다(frontend
readiness.ts의 raw-first 파싱 분기와 대응).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.core.observability.loop_health import LoopHealth, loop_health, set_loop_health
from src.main import app
from tests.conftest import lifespan_context_with_retry


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
