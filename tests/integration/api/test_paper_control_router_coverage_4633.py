"""통합테스트 — task-4633: `src/api/routers/foundation/paper_control.py` 라우터
레벨 HTTP 증빙 커버리지 보강.

`tests/integration/test_foundation_paper_control_risk_gate_router.py`가
start/kill-switch 경로만 다뤄, 이 라우터의 나머지 커맨드(request/resume/
pause/stop, list)와 실패 경로(cross-tenant, not-found, 인증 없음, 분류되지
않은 예외의 fail-closed 봉투화)에는 HTTP 경계 증빙이 없었다(D2 하한 미달,
ADR-2026-09-09-C Decision 1 — task-3179 DEEPEN과 동일 결함 패턴).

이 파일이 새로 고정하는 것:
- 라우터 레벨 HTTP 왕복 1건: request → start → pause → resume → stop 전체
  커맨드 사이클을 실제 ASGI 트랜스포트로 호출한다.
- negative test 3건: 인증 없는 요청(401), 존재하지 않는 deployment_id(404),
  cross-tenant 접근(404).
- 실패 주입 1건: `get_paper_control_repository` 의존성을 EXCEPTION_MAP에
  없는 예외를 던지는 이중으로 교체해, 전역 핸들러가 원본 메시지를 누출하지
  않고 고정 일반 메시지 + INTERNAL_ERROR로 fail-closed 봉투화하는지 확인한다.
- 수치 성능 단언: ADR-2026-09-09-C Decision 1에 paper-deployments 목록
  조회 전용 예산 항목이 없어, 가장 가까운 유사 항목("주문 제출→ACK p95
  50ms(paper)", 동일하게 단일 실DB 왕복)을 차용한다(task-3179 DEEPEN과
  동일 차용 근거) — `build_deployment_list_view()`(빈 테넌트, 단일 SELECT
  왕복) p95를 그 예산 내로 단언한다.
- 게이트 적색 재현: 위 p95 단언에 `asyncpg.Connection.fetch` 지연을 주입해
  실제로 AssertionError를 내는지 확인한다(tautology 아님을 증명).

Spec: docs/specs/L4_platform_paper_execution_control_v1.0.md
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.foundation_deps import get_paper_control_repository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.projections import build_deployment_list_view
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/paper-deployments"

DEFAULT_RULES = {
    "max_total_exposure_pct": 80.0,
    "max_single_instrument_pct": 20.0,
    "min_cash_buffer_pct": 5.0,
    "max_daily_loss_pct": 3.0,
    "allowed_autonomy": "PAPER",
    "forbidden_assets": ["XYZ"],
}


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await retry_too_many_connections(
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    )
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    app.dependency_overrides.clear()


async def _register(client: AsyncClient) -> tuple[dict, UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return headers, UUID(me.json()["data"]["user_id"])


async def _activate_mandate(client: AsyncClient, headers: dict) -> None:
    draft = (
        await client.post("/v1/foundation/mandates/drafts", json=DEFAULT_RULES, headers=headers)
    ).json()["data"]
    await client.post(
        f"/v1/foundation/mandates/revisions/{draft['id']}:activate", json={}, headers=headers
    )


async def _request_deployment(client: AsyncClient, headers: dict, *, key: str) -> str:
    response = await client.post(
        BASE,
        headers=headers,
        json={
            "package_ref": "pkg-coverage-test",
            "adapter_type": "fake-paper-v1",
            "provider_sandbox_account_ref": "sandbox-acct-coverage",
            "endpoint_classification": "SANDBOX",
            "idempotency_key": f"req-{key}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


# --- 라우터 레벨 HTTP 왕복: request -> start -> pause -> resume -> stop ---


async def test_full_command_cycle_round_trips_through_real_http(client):
    headers, _tenant_id = await _register(client)
    await _activate_mandate(client, headers)
    key = uuid.uuid4().hex
    deployment_id = await _request_deployment(client, headers, key=key)

    list_before = await client.get(BASE, headers=headers)
    assert list_before.status_code == 200
    assert [d["id"] for d in list_before.json()["data"]["deployments"]] == [deployment_id]

    start_response = await client.post(
        f"{BASE}/{deployment_id}:start",
        headers=headers,
        json={"idempotency_key": f"start-{key}"},
    )
    assert start_response.status_code == 200, start_response.text
    assert start_response.json()["data"]["state"] == "RUNNING"

    pause_response = await client.post(
        f"{BASE}/{deployment_id}:pause",
        headers=headers,
        json={"idempotency_key": f"pause-{key}"},
    )
    assert pause_response.status_code == 200, pause_response.text
    assert pause_response.json()["data"]["state"] == "PAUSED"

    resume_response = await client.post(
        f"{BASE}/{deployment_id}:resume",
        headers=headers,
        json={"idempotency_key": f"resume-{key}"},
    )
    assert resume_response.status_code == 200, resume_response.text
    assert resume_response.json()["data"]["state"] == "RUNNING"

    stop_response = await client.post(
        f"{BASE}/{deployment_id}:stop",
        headers=headers,
        json={"idempotency_key": f"stop-{key}"},
    )
    assert stop_response.status_code == 200, stop_response.text
    assert stop_response.json()["data"]["state"] == "STOPPED"


# --- negative 1: 인증 없는 요청은 401 ---


async def test_list_deployments_without_auth_is_401(client):
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_pause_without_auth_is_401(client):
    response = await client.post(
        f"{BASE}/{uuid.uuid4()}:pause", json={"idempotency_key": "no-auth"}
    )
    assert response.status_code == 401


# --- negative 2: 존재하지 않는 deployment_id는 404 ---


async def test_pause_on_nonexistent_deployment_is_404(client):
    headers, _tenant_id = await _register(client)
    response = await client.post(
        f"{BASE}/{uuid.uuid4()}:pause",
        headers=headers,
        json={"idempotency_key": uuid.uuid4().hex},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_stop_on_nonexistent_deployment_is_404(client):
    headers, _tenant_id = await _register(client)
    response = await client.post(
        f"{BASE}/{uuid.uuid4()}:stop",
        headers=headers,
        json={"idempotency_key": uuid.uuid4().hex},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


# --- negative 3: 다른 테넌트 소유 배포에 대한 커맨드는 404(존재 은닉) ---


async def test_pause_on_other_tenants_deployment_is_404(client):
    owner_headers, _owner_id = await _register(client)
    await _activate_mandate(client, owner_headers)
    key = uuid.uuid4().hex
    deployment_id = await _request_deployment(client, owner_headers, key=key)
    await client.post(
        f"{BASE}/{deployment_id}:start",
        headers=owner_headers,
        json={"idempotency_key": f"start-{key}"},
    )

    intruder_headers, _intruder_id = await _register(client)
    response = await client.post(
        f"{BASE}/{deployment_id}:pause",
        headers=intruder_headers,
        json={"idempotency_key": uuid.uuid4().hex},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


# --- negative 4: 잘못된 상태 전이(READY에서 pause)는 409 ---


async def test_pause_before_start_is_409(client):
    headers, _tenant_id = await _register(client)
    await _activate_mandate(client, headers)
    key = uuid.uuid4().hex
    deployment_id = await _request_deployment(client, headers, key=key)

    response = await client.post(
        f"{BASE}/{deployment_id}:pause",
        headers=headers,
        json={"idempotency_key": uuid.uuid4().hex},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


# --- 실패 주입: EXCEPTION_MAP에 없는 예외가 원본 메시지를 누출하지 않고 fail-closed로 봉투화 ---


class _LeakyPaperControlRepository:
    async def list_deployments(self, tenant_id: UUID) -> object:
        raise RuntimeError("connection string: postgresql://admin:hunter2@internal-db/prod")


async def test_list_deployments_unclassified_exception_is_enveloped_without_leaking(client):
    headers, _tenant_id = await _register(client)
    app.dependency_overrides[get_paper_control_repository] = lambda: _LeakyPaperControlRepository()
    try:
        response = await client.get(BASE, headers=headers)
    finally:
        del app.dependency_overrides[get_paper_control_repository]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 수치 성능 단언: build_deployment_list_view() 단일 SELECT 왕복 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(paper-deployments 목록 조회
# 전용 예산 항목 없음) — 둘 다 단일 실DB 왕복(빈 테넌트의 SELECT 1회)이라는
# 점에서 비교 가능한 부하 특성을 가진다(task-3179 DEEPEN과 동일 차용 근거).


async def _list_deployments_p95_ms(repo, tenant_id: UUID, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await build_deployment_list_view(repo, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_list_deployments_p95_under_borrowed_single_roundtrip_budget(client, pool):
    _headers, tenant_id = await _register(client)
    repo = PostgresPaperControlRepository(pool)

    p95_ms = await _list_deployments_p95_ms(repo, tenant_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_list_deployments_budget_gate_fails_on_injected_regression(client, pool, monkeypatch):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetch`에 60ms 인위 지연을 주입해, 같은 측정 로직이
    실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    _headers, tenant_id = await _register(client)
    repo = PostgresPaperControlRepository(pool)
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _list_deployments_p95_ms(repo, tenant_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
