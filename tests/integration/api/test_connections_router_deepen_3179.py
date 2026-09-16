"""통합테스트 — task-3179 DEEPEN: PLT-17~21(task-1108)
`src/api/routers/foundation/connections.py` 라우터 레벨 HTTP 증빙.

원 리프(task-1108, `tests/unit/api/test_envelope_everywhere.py` +
`tests/unit/api/test_no_raw_http_exception.py`)는 connections.py에 대해
"raw HTTPException 0건 + EXCEPTION_MAP 등록"이라는 정적 상태만 AST로
잠갔을 뿐, 실제로 FastAPI 앱을 띄워 `/v1/foundation/connections`를 HTTP로
때리는 테스트가 이 저장소에 단 한 건도 없었다(기존
`tests/foundation/integration/connections/test_connection_lifecycle.py`는
`begin_connection()` 등 애플리케이션 함수를 직접 호출할 뿐, 라우터·의존성
주입·전역 예외 핸들러를 한 번도 거치지 않는다) — 실패 주입도 수치 성능
단언도 0건이라 D2 하한(ADR-2026-09-09-C Decision 1) 미달이었다
(task-3160/3164/3174 DEEPEN과 동일 결함 패턴, 대상 라우터만 connections.py).

이 파일은 원 파일들을 건드리지 않고(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7) 다음을 새로 고정한다:

- 라우터 레벨 HTTP 왕복 1건: POST(begin) → GET(list) 전체를 실제 ASGI
  트랜스포트로 호출해 인증·의존성 주입·봉투 직렬화가 실제로 맞물리는지
  확인한다(이게 이 리프가 메우는 1차 공백이다).
- 실패 주입 1: MFA 비활성 세션으로 `POST /v1/foundation/connections`를
  호출하면(진짜 도메인 규칙, mock 아님) `begin_connection()`의
  `MfaRequiredError`가 EXCEPTION_MAP을 통해 403 AUTH_MFA_REQUIRED로
  봉투화되는지 확인한다.
- 실패 주입 2: `get_connection_repository` 의존성을 EXCEPTION_MAP에 없는
  예외(민감정보 포함)를 던지는 이중으로 교체해, 전역 핸들러가 원본 메시지를
  누출하지 않고 고정 일반 메시지 + INTERNAL_ERROR로 fail-closed 봉투화하는지
  확인한다.
- 수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 커넥션 목록 조회
  전용 항목이 없어 가장 가까운 유사 항목("주문 제출→ACK p95 50ms(paper)",
  동일하게 단일 실DB 왕복)을 자체 예산으로 차용한다(task-3148/3156/3160/
  3164/3174 DEEPEN과 동일 차용 근거) — `build_connection_list_view()`(빈
  테넌트, `list_connections` 단일 SELECT 왕복) p95를 그 예산 내로 단언한다.
- 게이트 적색 재현: 위 p95 단언에 `asyncpg.Connection.fetch` 지연을
  주입해 실제로 AssertionError를 내는지 확인한다(tautology 아님을 증명).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
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

from src.api.foundation_deps import get_connection_repository
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.connections.projections import build_connection_list_view
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.foundation.integration.connections.conftest import grant_account_read_consent

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/connections"


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


async def _enable_mfa(pool: asyncpg.Pool, user_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET mfa_enabled = true WHERE user_id = $1", user_id)


async def _grant_consent(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    trust_repo = PostgresTrustRepository(pool)
    await grant_account_read_consent(pool, trust_repo, tenant_id=tenant_id)


# --- 라우터 레벨 HTTP 왕복: 원래 존재하지 않았던 1차 공백을 메운다 ---


async def test_begin_then_list_connections_round_trips_through_real_http(client, pool):
    """`POST /v1/foundation/connections` → `GET /v1/foundation/connections`를
    실제 ASGI 트랜스포트로 호출해, 인증(get_current_user)·의존성 주입
    (get_connection_repository/get_trust_repository)·봉투 직렬화가 실제로
    맞물려 동작하는지 확인한다 — 이전에는 애플리케이션 함수를 직접 호출하는
    테스트만 있었을 뿐, 이 경로 전체를 HTTP로 왕복한 테스트가 없었다."""
    headers, tenant_id = await _register(client)
    await _enable_mfa(pool, tenant_id)
    await _grant_consent(pool, tenant_id)

    begin_response = await client.post(
        BASE,
        headers=headers,
        json={
            "provider_code": "fake-broker",
            "opaque_account_ref": "ACCT-1234567890",
            "requested_capability_profile": ["READ_BALANCE"],
        },
    )
    assert begin_response.status_code == 201
    begin_body = begin_response.json()["data"]
    assert begin_body["masked_account_label"] == "***********7890"
    assert begin_body["state"] == "PENDING_CONSENT"

    list_response = await client.get(BASE, headers=headers)
    assert list_response.status_code == 200
    list_body = list_response.json()["data"]
    assert [c["id"] for c in list_body["connections"]] == [begin_body["id"]]
    assert "as_of" in list_body


# --- 실패 주입 1: 실제 도메인 규칙(MFA 비활성)이 403으로 봉투화됨 ---


async def test_begin_connection_without_mfa_is_enveloped_as_403(client, pool):
    """실패 주입: MFA를 켜지 않은 세션으로 begin을 호출하면(진짜
    `begin_connection()`의 `MfaRequiredError`, mock 아님) EXCEPTION_MAP을
    통해 403 AUTH_MFA_REQUIRED로 봉투화되고, 커넥션이 생성되지 않는지
    확인한다."""
    headers, tenant_id = await _register(client)
    await _grant_consent(pool, tenant_id)  # 동의는 있어도 MFA가 없으면 여전히 막힌다

    response = await client.post(
        BASE,
        headers=headers,
        json={
            "provider_code": "fake-broker",
            "opaque_account_ref": "ACCT-1234567890",
            "requested_capability_profile": ["READ_BALANCE"],
        },
    )

    assert response.status_code == 403
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "AUTH_MFA_REQUIRED"

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM account_connection WHERE tenant_id = $1", tenant_id
        )
    assert count == 0


# --- 실패 주입 2: EXCEPTION_MAP에 없는 예외가 원본 메시지를 누출하지 않고 fail-closed로 봉투화 ---


class _LeakyConnectionRepository:
    async def list_connections(self, tenant_id: UUID) -> object:
        raise RuntimeError("connection string: postgresql://admin:hunter2@internal-db/prod")


async def test_list_connections_unclassified_exception_is_enveloped_without_leaking(client, pool):
    """실패 주입: `get_connection_repository` 의존성을 EXCEPTION_MAP에 없는
    예외를 던지는 이중으로 교체하면, 전역 핸들러가 이를 삼켜 위장 성공을
    반환하지 않으면서도 원본 예외 메시지(연결 문자열 등 민감정보일 수 있음)는
    누출하지 않고 고정 일반 메시지 + INTERNAL_ERROR로 봉투화하는지 확인한다
    (fail-closed)."""
    headers, _tenant_id = await _register(client)
    app.dependency_overrides[get_connection_repository] = lambda: _LeakyConnectionRepository()
    try:
        response = await client.get(BASE, headers=headers)
    finally:
        del app.dependency_overrides[get_connection_repository]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 수치 성능 단언: build_connection_list_view() 단일 SELECT 왕복 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(커넥션 목록 조회 전용 예산
# 항목 없음) — 둘 다 단일 실DB 왕복(빈 테넌트의 SELECT 1회)이라는 점에서
# 비교 가능한 부하 특성을 가진다(task-3148/3156/3160/3164/3174 DEEPEN과
# 동일 차용 근거).


async def _list_connections_p95_ms(repo: ConnectionRepository, tenant_id: UUID, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await build_connection_list_view(repo, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_list_connections_p95_under_borrowed_single_roundtrip_budget(client, pool):
    _headers, tenant_id = await _register(client)
    repo = PostgresConnectionRepository(pool)

    p95_ms = await _list_connections_p95_ms(repo, tenant_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_list_connections_budget_gate_fails_on_injected_regression(client, pool, monkeypatch):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetch`에 60ms 인위 지연을 주입해, 같은 측정 로직이
    실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    _headers, tenant_id = await _register(client)
    repo = PostgresConnectionRepository(pool)
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _list_connections_p95_ms(repo, tenant_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
