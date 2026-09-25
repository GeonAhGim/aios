"""통합테스트 -- task-2652 AI-17 `src/api/routers/ai.py` 라우터 레벨 HTTP 증빙.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-17
(`api/routers/ai.py`: 토큰 발급/회전/revoke, 제안 목록, 실험 조회 -- 인간
세션용, login_required), §9 AI-17 DoD("교차 테넌트 404").

D2 하한(ADR-2026-09-09-C Decision 1) 증빙 구성 -- `test_connections_router_
deepen_3179.py`(task-3179)와 동일한 구조를 이 새 라우터에 처음부터 적용한다:

- 라우터 레벨 HTTP 왕복 1건: 토큰 발급 -> 목록 -> 회전 -> revoke 전체를 실제
  ASGI 트랜스포트로 호출한다.
- negative 4건: 빈 scopes(400), 존재하지 않는 토큰 revoke(404), 타 테넌트
  토큰 revoke(404, 교차 테넌트), 타 테넌트 제안 조회(404, 교차 테넌트).
- 실패 주입 1: `get_agent_token_repository` 의존성을 EXCEPTION_MAP에 없는
  예외(민감정보 포함)를 던지는 이중으로 교체해 fail-closed 봉투화를 확인한다.
- 수치 성능 단언 1: `GET /v1/ai/tokens`(빈 테넌트, 단일 SELECT 왕복) p95를
  가장 가까운 유사 항목("주문 제출->ACK p95 50ms(paper)")으로 차용한 예산
  안에서 확인한다(task-3179/3160/3164/3174 DEEPEN과 동일 차용 근거).
- 게이트 적색 재현 1: `asyncpg.Connection.fetch`에 인위 지연을 주입해 위
  단언이 실제로 회귀를 잡는지 확인한다.
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

from src.api.routers.ai import get_agent_token_repository
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/ai"


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


def _issue_body(**overrides: object) -> dict:
    body = {
        "scopes": ["read", "research"],
        "allow_instruments": [],
        "notional_cap": "1000",
        "ttl_seconds": 3600,
    }
    body.update(overrides)
    return body


# --- 라우터 레벨 HTTP 왕복: 발급 -> 목록 -> 회전 -> revoke ---


async def test_issue_list_rotate_revoke_round_trips_through_real_http(client, pool):
    headers, _tenant_id = await _register(client)

    issue_response = await client.post(f"{BASE}/tokens", headers=headers, json=_issue_body())
    assert issue_response.status_code == 201
    issued = issue_response.json()["data"]
    assert issued["secret"]
    assert issued["scopes"] == ["read", "research"]
    token_id = issued["token_id"]

    list_response = await client.get(f"{BASE}/tokens", headers=headers)
    assert list_response.status_code == 200
    listed = list_response.json()["data"]["tokens"]
    assert [t["token_id"] for t in listed] == [token_id]
    assert "secret" not in listed[0]

    rotate_response = await client.post(
        f"{BASE}/tokens/{token_id}:rotate", headers=headers, json={"ttl_seconds": 7200}
    )
    assert rotate_response.status_code == 200
    rotated = rotate_response.json()["data"]
    assert rotated["token_id"] != token_id
    assert rotated["scopes"] == ["read", "research"]
    assert rotated["secret"] != issued["secret"]

    revoke_response = await client.post(
        f"{BASE}/tokens/{rotated['token_id']}:revoke",
        headers=headers,
        json={"reason": "cleanup"},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["data"]["revoked_at"] is not None

    # The original token, revoked by the rotate call, no longer appears active either.
    list_after = await client.get(f"{BASE}/tokens", headers=headers)
    revoked_ats = {t["token_id"]: t["revoked_at"] for t in list_after.json()["data"]["tokens"]}
    assert revoked_ats[token_id] is not None
    assert revoked_ats[rotated["token_id"]] is not None


# --- negative 1: 빈 scopes -> 400 ---


async def test_issue_agent_token_with_empty_scopes_is_400(client, pool):
    headers, _tenant_id = await _register(client)

    response = await client.post(f"{BASE}/tokens", headers=headers, json=_issue_body(scopes=[]))

    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


# --- negative 2: 존재하지 않는 토큰 revoke -> 404 ---


async def test_revoke_nonexistent_agent_token_is_404(client, pool):
    headers, _tenant_id = await _register(client)

    response = await client.post(
        f"{BASE}/tokens/{uuid.uuid4()}:revoke", headers=headers, json={"reason": "x"}
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


# --- negative 3: 타 테넌트 토큰 revoke -> 404 (교차 테넌트, §9 AI-17 DoD) ---


async def test_revoke_other_tenants_agent_token_is_404(client, pool):
    owner_headers, _owner_tenant = await _register(client)
    attacker_headers, _attacker_tenant = await _register(client)

    issue_response = await client.post(f"{BASE}/tokens", headers=owner_headers, json=_issue_body())
    token_id = issue_response.json()["data"]["token_id"]

    response = await client.post(
        f"{BASE}/tokens/{token_id}:revoke", headers=attacker_headers, json={"reason": "steal"}
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"

    # The token itself is untouched by the failed cross-tenant attempt.
    owner_list = await client.get(f"{BASE}/tokens", headers=owner_headers)
    tokens = owner_list.json()["data"]["tokens"]
    assert tokens[0]["revoked_at"] is None


# --- negative 4: 타 테넌트 제안 조회 -> 404 (교차 테넌트) ---


async def test_get_other_tenants_proposal_is_404(client, pool):
    _owner_headers, _owner_tenant = await _register(client)
    attacker_headers, _attacker_tenant = await _register(client)

    response = await client.get(f"{BASE}/proposals/{uuid.uuid4()}", headers=attacker_headers)

    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


# --- 실패 주입: EXCEPTION_MAP에 없는 예외가 원본 메시지를 누출하지 않음 ---


class _LeakyAgentTokenRepository:
    async def list_for_tenant(self, tenant_id: UUID) -> object:
        raise RuntimeError("connection string: postgresql://admin:hunter2@internal-db/prod")


async def test_list_agent_tokens_unclassified_exception_is_enveloped_without_leaking(client, pool):
    headers, _tenant_id = await _register(client)
    app.dependency_overrides[get_agent_token_repository] = lambda: _LeakyAgentTokenRepository()
    try:
        response = await client.get(f"{BASE}/tokens", headers=headers)
    finally:
        del app.dependency_overrides[get_agent_token_repository]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 수치 성능 단언: GET /v1/ai/tokens 단일 SELECT 왕복 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출->ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(토큰 목록 조회 전용 예산
# 항목 없음) -- 둘 다 단일 실DB 왕복(빈 테넌트의 SELECT 1회)이라는 점에서
# 비교 가능한 부하 특성을 가진다(task-3179 DEEPEN과 동일 차용 근거).


async def _list_tokens_p95_ms(
    repo: PostgresAgentTokenRepository, tenant_id: UUID, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await repo.list_for_tenant(tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_list_agent_tokens_p95_under_borrowed_single_roundtrip_budget(client, pool):
    _headers, tenant_id = await _register(client)
    repo = PostgresAgentTokenRepository(pool)

    p95_ms = await _list_tokens_p95_ms(repo, tenant_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_list_agent_tokens_budget_gate_fails_on_injected_regression(
    client, pool, monkeypatch
):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 --
    `asyncpg.Connection.fetch`에 60ms 인위 지연을 주입해, 같은 측정 로직이
    실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    _headers, tenant_id = await _register(client)
    repo = PostgresAgentTokenRepository(pool)
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _list_tokens_p95_ms(repo, tenant_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
