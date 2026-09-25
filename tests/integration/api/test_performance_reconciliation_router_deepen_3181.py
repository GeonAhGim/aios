"""통합테스트 — task-3181 DEEPEN: PLT-17~21(task-1217)
`src/api/routers/foundation/performance.py` /
`src/api/routers/foundation/reconciliation.py` 라우터 레벨 HTTP 증빙.

원 리프(task-1217, `tests/unit/api/test_envelope_everywhere.py` +
`tests/unit/api/test_no_raw_http_exception.py`)는 이 세 라우터(paper_control/
performance/reconciliation)에 대해 "raw HTTPException 0건 + EXCEPTION_MAP
전부 등록"이라는 정적 상태만 AST로 잠갔다. paper_control.py는
`tests/integration/test_foundation_paper_control_risk_gate_router.py`가 실제
HTTP 왕복과 negative(에러코드) 테스트 1건(409, already-running start)을
갖고 있었지만, performance.py/reconciliation.py는 실제 ASGI 트랜스포트로
때리는 테스트가 이 저장소에 단 한 건도 없었다 — 기존
`tests/foundation/integration/performance/*`, `tests/foundation/integration/
reconciliation/*`는 애플리케이션 함수(compute_statement/run_reconciliation
등)를 직접 호출할 뿐, 라우터·의존성주입·전역 예외 핸들러를 거치지 않는다.
negative(에러코드) 테스트가 전체 스코프에 1건뿐(3건 필요), 실패 주입 0건,
수치 성능 단언 0건이라 D2 하한(ADR-2026-09-09-C Decision 1) 미달이었다
(task-3160/3164/3174/3179 DEEPEN과 동일 결함 패턴).

이 파일은 원 파일들을 건드리지 않고(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7) 다음을 새로 고정한다:

- 라우터 레벨 HTTP 왕복 2건: performance.py(POST :compute → GET list → GET
  단건), reconciliation.py(POST /runs → GET list → POST :resolve) 각각 실제
  ASGI 트랜스포트로 전체 경로를 왕복한다(이 리프가 메우는 1차 공백).
- negative(에러코드) 테스트 3건: GET 없는 statement_id(404
  RESOURCE_NOT_FOUND), POST :compute scope=LIVE(400
  VALIDATION_INVALID_FIELD), HEALTHY 상태를 :resolve(409
  STATE_INVALID_TRANSITION) — 이전 1건(다른 파일)과 합쳐 D2 하한(≥3) 충족.
- 실패 주입 1: reconciliation_state 행이 없는(미리컨실) 테넌트로 :compute를
  호출하면(진짜 `UnreconciledInputError`, mock 아님) 409
  STATE_INVALID_TRANSITION으로 봉투화되는지 확인한다.
- 실패 주입 2: `get_reconciliation_repository` 의존성을 EXCEPTION_MAP에 없는
  예외(민감정보 포함)를 던지는 이중으로 교체해, 전역 핸들러가 원본 메시지를
  누출하지 않고 고정 일반 메시지 + INTERNAL_ERROR로 fail-closed 봉투화하는지
  확인한다.
- 수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 reconciliation 상태
  목록 조회 전용 항목이 없어 가장 가까운 유사 항목("주문 제출→ACK p95
  50ms(paper)", 동일하게 단일 실DB 왕복)을 자체 예산으로 차용한다
  (task-3148/3156/3160/3164/3174/3179 DEEPEN과 동일 차용 근거) —
  `build_reconciliation_state_list_view()`(단일 SELECT 왕복) p95를 그 예산
  내로 단언한다.
- 게이트 적색 재현: 위 p95 단언에 `asyncpg.Connection.fetch` 지연을 주입해
  실제로 AssertionError를 내는지 확인한다(tautology 아님을 증명).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.foundation_deps import get_reconciliation_repository
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.ports.repository import ReconciliationRepository
from src.foundation.reconciliation.projections import build_reconciliation_state_list_view
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.foundation.integration.performance.conftest import set_reconciliation_state

STRONG_PASSWORD = "Str0ng!Passw0rd"
PERF_BASE = "/v1/foundation/performance-statements"
REC_BASE = "/v1/foundation/reconciliation"

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


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


def _compute_body(scope: str = "PAPER") -> dict:
    return {
        "scope": scope,
        "period_start": _PERIOD_START.isoformat(),
        "period_end": _NOW.isoformat(),
    }


def _matching_entities() -> list[dict]:
    return [
        {
            "entity_type": "BALANCE",
            "entity_key": "USDT",
            "internal_value": "1000.00",
            "provider_value": "1000.00",
        }
    ]


def _mismatched_entities() -> list[dict]:
    return [
        {
            "entity_type": "BALANCE",
            "entity_key": "USDT",
            "internal_value": "1000.00",
            "provider_value": "400.00",
        }
    ]


async def _run_reconciliation(
    client: AsyncClient, headers: dict, tenant_id: UUID, entities: list[dict]
) -> dict:
    response = await client.post(
        f"{REC_BASE}/runs",
        headers=headers,
        json={
            "target_type": "PAPER_DEPLOYMENT",
            "target_ref": str(tenant_id),
            "entities": entities,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --- 라우터 레벨 HTTP 왕복 1: performance.py — 원래 존재하지 않았던 1차 공백 ---


async def test_compute_then_get_and_list_statement_round_trips_through_real_http(client, pool):
    """`POST :compute` → `GET` 단건 → `GET` 목록을 실제 ASGI 트랜스포트로
    호출해, 인증·의존성 주입·봉투 직렬화가 실제로 맞물려 동작하는지 확인한다
    — 이전에는 애플리케이션 함수를 직접 호출하는 테스트만 있었을 뿐, 이
    경로 전체를 HTTP로 왕복한 테스트가 없었다."""
    headers, tenant_id = await _register(client)
    await set_reconciliation_state(pool, tenant_id, aggregate_status="HEALTHY")

    compute_response = await client.post(
        f"{PERF_BASE}:compute", headers=headers, json=_compute_body()
    )
    assert compute_response.status_code == 202, compute_response.text
    statement = compute_response.json()["data"]
    assert statement["state"] == "ESTIMATED"

    get_response = await client.get(f"{PERF_BASE}/{statement['id']}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["data"]["id"] == statement["id"]

    list_response = await client.get(PERF_BASE, headers=headers)
    assert list_response.status_code == 200
    listed_ids = [s["id"] for s in list_response.json()["data"]["statements"]]
    assert statement["id"] in listed_ids


# --- 라우터 레벨 HTTP 왕복 2: reconciliation.py — 원래 존재하지 않았던 1차 공백 ---


async def test_run_then_list_then_resolve_reconciliation_round_trips_through_real_http(
    client, pool
):
    """`POST /runs` → `GET` 목록 → `POST :resolve`를 실제 ASGI 트랜스포트로
    호출해, MATERIAL_MISMATCH 판정이 목록 프로젝션에 반영되고 resolve가
    RESOLVED로 전이시키는지 확인한다."""
    headers, tenant_id = await _register(client)

    run = await _run_reconciliation(client, headers, tenant_id, _mismatched_entities())
    assert run["aggregate_classification"] == "MATERIAL_MISMATCH"

    list_response = await client.get(REC_BASE, headers=headers)
    assert list_response.status_code == 200
    states = list_response.json()["data"]["states"]
    matching = [s for s in states if s["target_ref"] == str(tenant_id)]
    assert len(matching) == 1
    assert matching[0]["aggregate_status"] == "MATERIAL_MISMATCH"

    resolve_response = await client.post(
        f"{REC_BASE}/{tenant_id}:resolve",
        headers=headers,
        json={"reason": "원인 파악 완료, 수동 정정함"},
    )
    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["data"]["aggregate_status"] == "RESOLVED"


# --- negative(에러코드) 1: 없는 statement_id는 404 ---


async def test_get_nonexistent_performance_statement_is_404(client):
    headers, _tenant_id = await _register(client)

    response = await client.get(f"{PERF_BASE}/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "RESOURCE_NOT_FOUND"


# --- negative(에러코드) 2: scope=LIVE는 아직 배선되지 않아 400 ---


async def test_compute_statement_with_live_scope_is_400(client):
    headers, _tenant_id = await _register(client)

    response = await client.post(
        f"{PERF_BASE}:compute", headers=headers, json=_compute_body("LIVE")
    )

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"


# --- negative(에러코드) 3: HEALTHY 상태는 resolve 대상이 아니라 409 ---


async def test_resolve_healthy_reconciliation_state_is_409_not_resolvable(client):
    headers, tenant_id = await _register(client)
    run = await _run_reconciliation(client, headers, tenant_id, _matching_entities())
    assert run["aggregate_classification"] == "HEALTHY"

    response = await client.post(
        f"{REC_BASE}/{tenant_id}:resolve",
        headers=headers,
        json={"reason": "이미 정상인데 resolve 시도"},
    )

    assert response.status_code == 409
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "STATE_INVALID_TRANSITION"


# --- 실패 주입 1: 실제 도메인 규칙(미리컨실 입력)이 409로 봉투화됨 ---


async def test_compute_statement_without_reconciliation_state_is_409_unreconciled(client, pool):
    """실패 주입: reconciliation_state 행이 아예 없는(미리컨실) 테넌트로
    `:compute`를 호출하면(진짜 `UnreconciledInputError`, mock 아님)
    EXCEPTION_MAP을 통해 409 STATE_INVALID_TRANSITION으로 봉투화되는지
    확인한다."""
    headers, tenant_id = await _register(client)

    response = await client.post(f"{PERF_BASE}:compute", headers=headers, json=_compute_body())

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "STATE_INVALID_TRANSITION"

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM performance_statement WHERE tenant_id = $1", tenant_id
        )
    assert count == 0


# --- 실패 주입 2: EXCEPTION_MAP에 없는 예외가 원본 메시지를 누출하지 않고 fail-closed로 봉투화 ---


class _LeakyReconciliationRepository:
    async def list_states(self, tenant_id: UUID) -> object:
        raise RuntimeError("connection string: postgresql://admin:hunter2@internal-db/prod")


async def test_list_reconciliation_states_unclassified_exception_is_enveloped_without_leaking(
    client,
):
    """실패 주입: `get_reconciliation_repository` 의존성을 EXCEPTION_MAP에
    없는 예외를 던지는 이중으로 교체하면, 전역 핸들러가 이를 삼켜 위장 성공을
    반환하지 않으면서도 원본 예외 메시지(연결 문자열 등 민감정보일 수 있음)는
    누출하지 않고 고정 일반 메시지 + INTERNAL_ERROR로 봉투화하는지 확인한다
    (fail-closed)."""
    headers, _tenant_id = await _register(client)
    app.dependency_overrides[get_reconciliation_repository] = lambda: (
        _LeakyReconciliationRepository()
    )
    try:
        response = await client.get(REC_BASE, headers=headers)
    finally:
        del app.dependency_overrides[get_reconciliation_repository]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 수치 성능 단언: build_reconciliation_state_list_view() 단일 SELECT 왕복 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(reconciliation 상태 목록
# 조회 전용 예산 항목 없음) — 둘 다 단일 실DB 왕복(빈 테넌트의 SELECT 1회)
# 이라는 점에서 비교 가능한 부하 특성을 가진다(task-3148/3156/3160/3164/
# 3174/3179 DEEPEN과 동일 차용 근거).


async def _list_states_p95_ms(repo: ReconciliationRepository, tenant_id: UUID, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await build_reconciliation_state_list_view(repo, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_list_reconciliation_states_p95_under_borrowed_single_roundtrip_budget(client, pool):
    _headers, tenant_id = await _register(client)
    repo = PostgresReconciliationRepository(pool)

    p95_ms = await _list_states_p95_ms(repo, tenant_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_list_reconciliation_states_budget_gate_fails_on_injected_regression(
    client, pool, monkeypatch
):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetch`에 60ms 인위 지연을 주입해, 같은 측정 로직이
    실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    _headers, tenant_id = await _register(client)
    repo = PostgresReconciliationRepository(pool)
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _list_states_p95_ms(repo, tenant_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
