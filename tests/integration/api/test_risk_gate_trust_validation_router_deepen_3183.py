"""통합테스트 — task-3183 DEEPEN: PLT-17~21(task-1218)
`src/api/routers/foundation/risk_gate.py` / `trust.py` / `validation.py`
라우터의 실패 주입·수치 성능 단언·게이트 적색 재현 증빙.

원 리프(task-1218, `tests/unit/api/test_envelope_everywhere.py` +
`tests/unit/api/test_no_raw_http_exception.py`)는 이 세 라우터에 대해 "raw
HTTPException 0건 + EXCEPTION_MAP 전부 등록"이라는 정적 상태만 AST로 잠갔다.
negative(에러코드) 테스트는 `tests/integration/test_foundation_trust_router.py`
(401/404/403 3건), `tests/integration/test_foundation_validation_router.py`
(401/409 2건)가 이미 D2 하한(≥3)을 충족하지만, 실패 주입 0건, 수치 성능
단언 0건, 게이트 적색 재현 0건이라 D2 하한(ADR-2026-09-09-C Decision 1)
미달이었다(task-3160/3164/3174/3179/3181 DEEPEN과 동일 결함 패턴, 대상만
risk_gate/trust/validation).

이 파일은 원 파일들을 건드리지 않고(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7) 다음을 새로 고정한다:

- 실패 주입 1: 다른 테넌트 소유의 connection_id로 `POST
  /v1/foundation/risk-gate/evaluate`를 호출하면(진짜
  `CrossTenantConnectionReferenceError`, mock 아님) EXCEPTION_MAP을 통해 404
  RESOURCE_NOT_FOUND로 봉투화되고, 그 테넌트에 risk_evaluation 행이 남지
  않는지 확인한다(계정 존재 열거를 막는 fail-closed 판단이 부작용도 남기지
  않는지).
- 실패 주입 2: `get_trust_repository` 의존성을 EXCEPTION_MAP에 없는 예외
  (민감정보 포함)를 던지는 이중으로 교체해, 전역 핸들러가 원본 메시지를
  누출하지 않고 고정 일반 메시지 + INTERNAL_ERROR로 fail-closed 봉투화하는지
  확인한다.
- 수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표의 "사전거래 게이트 p99
  5ms"가 risk_gate PRE_TRADE 게이트에 직접 대응하는 항목이라(차용 아님)
  `evaluate_risk_gate()`(캐시 히트 정상 상태, `risk_evaluation`/
  `portfolio_mandate` 각 단일 SELECT 왕복) p99를 그 예산 내로 단언한다.
- 게이트 적색 재현: 위 p99 단언에 `asyncpg.Connection.fetchrow` 지연을
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

from src.api.foundation_deps import get_trust_repository
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.foundation.integration.connections.conftest import grant_account_read_consent

STRONG_PASSWORD = "Str0ng!Passw0rd"
RISK_GATE_BASE = "/v1/foundation/risk-gate"
CONNECTIONS_BASE = "/v1/foundation/connections"
TRUST_BASE = "/v1/foundation/trust"


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


async def _begin_connection(client: AsyncClient, headers: dict) -> str:
    response = await client.post(
        CONNECTIONS_BASE,
        headers=headers,
        json={
            "provider_code": "fake-broker",
            "opaque_account_ref": "ACCT-1234567890",
            "requested_capability_profile": ["READ_BALANCE"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


# --- 실패 주입 1: 실제 도메인 규칙(다른 테넌트의 connection_id)이 404로 봉투화됨 ---


async def test_evaluate_with_other_tenants_connection_id_is_404_cross_tenant(client, pool):
    """실패 주입: 테넌트 B가 테넌트 A 소유 connection_id로 risk-gate
    `:evaluate`를 호출하면(진짜 `CrossTenantConnectionReferenceError`, mock
    아님) EXCEPTION_MAP을 통해 404 RESOURCE_NOT_FOUND로 봉투화되고, 어느
    쪽 테넌트에도 risk_evaluation 행이 남지 않는지 확인한다."""
    owner_headers, owner_id = await _register(client)
    await _enable_mfa(pool, owner_id)
    await _grant_consent(pool, owner_id)
    connection_id = await _begin_connection(client, owner_headers)

    attacker_headers, attacker_id = await _register(client)

    response = await client.post(
        f"{RISK_GATE_BASE}/evaluate",
        headers=attacker_headers,
        json={"gate_kind": "PRE_TRADE", "connection_id": connection_id},
    )

    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "RESOURCE_NOT_FOUND"

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_evaluation WHERE tenant_id IN ($1, $2)",
            owner_id,
            attacker_id,
        )
    assert count == 0


# --- 실패 주입 2: EXCEPTION_MAP에 없는 예외가 원본 메시지를 누출하지 않고 fail-closed로 봉투화 ---


class _LeakyTrustRepository:
    async def list_active_consents(self, tenant_id: UUID) -> object:
        raise RuntimeError("connection string: postgresql://admin:hunter2@internal-db/prod")


async def test_get_trust_status_unclassified_exception_is_enveloped_without_leaking(client):
    """실패 주입: `get_trust_repository` 의존성을 EXCEPTION_MAP에 없는 예외를
    던지는 이중으로 교체하면, 전역 핸들러가 이를 삼켜 위장 성공을 반환하지
    않으면서도 원본 예외 메시지(연결 문자열 등 민감정보일 수 있음)는 누출하지
    않고 고정 일반 메시지 + INTERNAL_ERROR로 봉투화하는지 확인한다
    (fail-closed)."""
    headers, _tenant_id = await _register(client)
    app.dependency_overrides[get_trust_repository] = lambda: _LeakyTrustRepository()
    try:
        response = await client.get(f"{TRUST_BASE}/status", headers=headers)
    finally:
        del app.dependency_overrides[get_trust_repository]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 수치 성능 단언: evaluate_risk_gate() 캐시 히트 정상 상태 p99 ---

_PERF_ITERATIONS = 100
_PERF_BUDGET_MS = 5.0  # ADR-2026-09-09-C Decision 1 "사전거래 게이트 p99
# 5ms"에 직접 대응하는 항목(risk_gate PRE_TRADE 게이트)이라 다른 DEEPEN
# 리프들과 달리 차용하지 않는다 — `evaluate_risk_gate()`는 mandate 없음
# (NO_MANDATE) + connection_id=None 조합에서 캐시 히트 시
# `portfolio_mandate`/`risk_evaluation` 각 단일 SELECT 왕복 2건뿐이라(첫
# 호출로 캐시를 채운 뒤 측정) 이 예산에 부합하는 부하 특성이다.


async def _evaluate_p99_ms(
    repo: PostgresRiskGateRepository,
    mandate_repo: PostgresMandateRepository,
    connection_repo: PostgresConnectionRepository,
    tenant_id: UUID,
    *,
    n: int,
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=tenant_id,
            gate_kind=GateKind.PRE_TRADE,
        )
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.99)]


async def _delete_risk_evaluations(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    # task-4042 -- evaluate_risk_gate()'s warmup call commits a real
    # risk_evaluation row (PRE_TRADE, outside the 6-value CHECK's old
    # 2-value predecessor). Left uncleaned, it survives past this test and
    # makes any later full-suite migration round trip that downgrades
    # f4b9d6e5a7c8 fail with CheckViolationError when it tries to restore
    # the old 2-value CHECK -- the same failure mode
    # test_risk_gate_lifecycle.py::test_gate_kind_check_accepts_all_six_values
    # already guards against by deleting what it inserts.
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM risk_evaluation WHERE tenant_id = $1", tenant_id)


@pytest.mark.perf
async def test_evaluate_risk_gate_p99_under_pre_trade_gate_budget(client, pool):
    _headers, tenant_id = await _register(client)
    repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)
    connection_repo = PostgresConnectionRepository(pool)

    try:
        # 캐시를 채우는 워밍업 호출 — 이 예산은 캐시 히트(steady-state) 지연을
        # 잰다(첫 호출은 mandate 정책 평가·list_active_controls·insert까지 더
        # 든다).
        await evaluate_risk_gate(
            repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.PRE_TRADE
        )

        p99_ms = await _evaluate_p99_ms(
            repo, mandate_repo, connection_repo, tenant_id, n=_PERF_ITERATIONS
        )

        assert p99_ms < _PERF_BUDGET_MS
    finally:
        await _delete_risk_evaluations(pool, tenant_id)


@pytest.mark.perf
async def test_evaluate_risk_gate_budget_gate_fails_on_injected_regression(
    client, pool, monkeypatch
):
    """게이트 적색 재현: 위 p99 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetchrow`에 10ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    _headers, tenant_id = await _register(client)
    repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)
    connection_repo = PostgresConnectionRepository(pool)
    try:
        await evaluate_risk_gate(
            repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.PRE_TRADE
        )

        original_fetchrow = asyncpg.Connection.fetchrow

        async def _slow_fetchrow(
            self: asyncpg.Connection, *args: object, **kwargs: object
        ) -> object:
            await asyncio.sleep(0.01)
            return await original_fetchrow(self, *args, **kwargs)

        monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

        p99_ms = await _evaluate_p99_ms(repo, mandate_repo, connection_repo, tenant_id, n=5)

        with pytest.raises(AssertionError):
            assert p99_ms < _PERF_BUDGET_MS
    finally:
        await _delete_risk_evaluations(pool, tenant_id)
