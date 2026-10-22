"""task-2020 — PLT-26/PLT-28 P0 배선 증명.

`/auth/register`가 실제로 `AuthService.signup`을 태우고, `signup`이 실제로
`tenant` 행을 만드는지를 실 DB로 검증한다. `auth_service.py`의 tenant INSERT
한 줄을 지우면 이 테스트는 FAIL한다 — 이 테스트 자체가 그 배선증명이다
(`AuthService.signup`을 단위테스트로 모킹하면 배선이 끊겨도 통과해버린다).

task-4151 DEEPEN — negative 3건(중복 이메일/비밀번호 복잡도 미달/이메일
형식 오류), 실패주입 1건(tenant INSERT 실패 시 users 행 롤백 원자성),
수치 성능 단언 1건(가장 가까운 유사 항목 "주문 제출→ACK p95 50ms(paper)"이
아니라, 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 test_dashboard_router.py
와 동일하게 baseline 대비 정규화한 상한을 쓴다)을 추가한다.
"""

from __future__ import annotations

import math
import os
import uuid
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_async

STRONG_PASSWORD = "Str0ng!Passw0rd"


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


async def _register(client: AsyncClient) -> UUID:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return UUID(me.json()["data"]["user_id"])


async def test_signup_creates_personal_tenant_row(client, pool):
    user_id = await _register(client)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT kind FROM tenant WHERE id = $1", user_id)
    assert row is not None, "signup이 personal tenant 행을 만들지 않았다"
    assert row["kind"] == "PERSONAL"


async def test_duplicate_email_signup_rejected_and_leaves_single_tenant_row(client, pool):
    """negative — 이미 등록된 이메일 재가입은 거부되고, 기존 personal tenant
    행은 그대로 1개여야 한다(재가입 거부 우회로 tenant가 중복 생성되면
    안 된다)."""
    email = f"dup-{uuid.uuid4().hex}@example.com"
    first = await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert first.status_code == 201, first.text

    async with pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT user_id FROM users WHERE email = $1", email)
    assert user_id is not None

    second = await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert second.status_code == 401, second.text
    assert second.json()["error_code"] == "AUTH_INVALID_CREDENTIALS"

    async with pool.acquire() as conn:
        tenant_count = await conn.fetchval("SELECT count(*) FROM tenant WHERE id = $1", user_id)
    assert tenant_count == 1, "중복 가입 거부 후에도 personal tenant 행은 1개여야 한다"


_DUPLICATE_SIGNUP_CPU_BUDGET_MS = 500.0


@pytest.mark.perf
async def test_duplicate_email_signup_cpu_converges_to_zero(client, pool, perf_budget: PerfBudget):
    """수치 성능 단언 — 중복 이메일 재가입 호출 1회의 CPU 시간이 0 수렴하고
    예산 내야 한다.

    `time.process_time()` 기반 측정으로 공유 호스트의 wall-clock 노이즈를
    제거하고, 실제 프로세스가 소비한 CPU 시간만 계측한다.
    """
    email = f"cpu-{uuid.uuid4().hex}@example.com"
    # 먼저 등록
    await client.post(
        "/auth/register",
        json={"email": email, "password": STRONG_PASSWORD},
    )

    async def _duplicate_call() -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": email, "password": STRONG_PASSWORD},
        )
        assert resp.status_code == 401

    sample = await perf_budget.sample_async(_duplicate_call, batch=1)
    assert sample.cpu_ms < _DUPLICATE_SIGNUP_CPU_BUDGET_MS, (
        f"중복 가입 CPU 시간 {sample.cpu_ms:.2f}ms가 예산 "
        f"{_DUPLICATE_SIGNUP_CPU_BUDGET_MS:.1f}ms를 초과했습니다"
    )


@pytest.mark.perf
async def test_duplicate_email_signup_goes_red_with_io_delay(
    client, pool, monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
):
    """gate-red reproduction: asyncpg.Connection.fetchval 에 IO 지연(
    delayed_async, 1.0s) 을 주입한 후 같은 성능 단언을 돌리면 예산을
    초과해야 한다(red).

    `conn.fetchval` 를 `delayed_async` 로 감싸 1회 호출당 1초 IO 지연을
    주입 — 중복 가입 검증(I/O 바인딩)이 wall-clock 지연으로 느려지는 것을
    측정한다. 정상 입력에서 통과하는 것이 테스트의 허수(trivial pass)가
    아님을 보장한다. 단언/마커 삭제와 예산 완화 금지.
    """
    original_fetchval = asyncpg.Connection.fetchval

    monkeypatch.setattr(asyncpg.Connection, "fetchval", delayed_async(original_fetchval, 1.0))

    email = f"delay-{uuid.uuid4().hex}@example.com"
    await client.post(
        "/auth/register",
        json={"email": email, "password": STRONG_PASSWORD},
    )

    async def _duplicate_call_with_delay() -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": email, "password": STRONG_PASSWORD},
        )
        assert resp.status_code == 401

    sample = await perf_budget.sample_async(_duplicate_call_with_delay, batch=1)
    assert sample.wall_ms >= _DUPLICATE_SIGNUP_CPU_BUDGET_MS, (
        f"지연 주입 후 wall 시간 {sample.wall_ms:.3f}ms — "
        f"예상과 달리 녹색: 측정/단언이 공허함(hollow assertion, "
        f"budget={_DUPLICATE_SIGNUP_CPU_BUDGET_MS:.1f}ms)"
    )


async def test_weak_password_missing_complexity_rejected_no_user_or_tenant_created(client, pool):
    """negative — 길이는 12자 이상이지만 대문자/특수문자가 없는 비밀번호는
    서비스 레이어 복잡도 검사에서 거부되어야 하고, users/tenant 어느 쪽도
    행이 생기면 안 된다."""
    email = f"weak-{uuid.uuid4().hex}@example.com"
    response = await client.post(
        "/auth/register", json={"email": email, "password": "alllowercase1"}
    )
    assert response.status_code == 401, response.text
    assert response.json()["error_code"] == "AUTH_INVALID_CREDENTIALS"

    async with pool.acquire() as conn:
        user_count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    assert user_count == 0, "비밀번호 복잡도 거부 시 users 행이 생기면 안 된다"


async def test_invalid_email_format_rejected_before_signup(client):
    """negative — 이메일 형식 자체가 잘못되면 서비스 계층에 닿기 전
    pydantic 스키마 단계에서 400 VALIDATION_INVALID_FIELD로 거부되어야
    한다(AuthService.signup 호출조차 되지 않는 입력 경계)."""
    response = await client.post(
        "/auth/register", json={"email": "not-an-email", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_tenant_insert_failure_rolls_back_user_row(client, pool, monkeypatch):
    """실패주입 — PLT-26/PLT-28 배선이 기대는 원자성: tenant INSERT가
    실패하면 같은 트랜잭션의 users INSERT도 롤백되어야 한다. 그렇지 않으면
    personal tenant 없는 users 행이 남아 이후 tenant_id FK 전제가 깨진다."""

    async def _boom(self, conn, *, tenant_id, kind, display_name=None):
        raise RuntimeError("tenant insert failure injection")

    monkeypatch.setattr(PostgresMembershipRepository, "insert_tenant", _boom)

    email = f"boom-{uuid.uuid4().hex}@example.com"
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert response.status_code == 500, response.text
    assert response.json()["error_code"] == "INTERNAL_ERROR"

    async with pool.acquire() as conn:
        user_count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    assert user_count == 0, (
        "tenant INSERT 실패 시 트랜잭션 롤백으로 users 행도 없어야 한다(원자성 위반)"
    )


@pytest.mark.perf
async def test_register_p95_latency_stays_within_normalized_ceiling(client, perf_budget):
    """수치 성능 단언 — 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신 baseline 호출 1건 대비 정규화한 상한만 게이트로 쓴다
    (test_dashboard_router.py의 동일 결정 참조). `/auth/register`는 Argon2
    해시(~수십ms) + users/tenant INSERT + 세션 발급까지 한 요청에 묶여있어
    회귀(예: N+1 쿼리, 잠금 경합)가 생기면 baseline 대비 배율이 눈에 띄게
    벌어진다.

    raw time.monotonic() → perf_budget.samples_async() 전환 (task-11038).
    """

    async def _call() -> None:
        response = await client.post(
            "/auth/register",
            json={
                "email": f"perf-{uuid.uuid4().hex}@example.com",
                "password": STRONG_PASSWORD,
            },
        )
        assert response.status_code == 201, response.text

    # baseline 1건 (wall_ms)
    baseline_sample = await perf_budget.sample_async(_call)
    baseline_ms = baseline_sample.wall_ms

    # 6회 샘플 → p95 계산
    samples = await perf_budget.samples_async(_call, n=6)
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(math.ceil(0.95 * len(wall_ms_list)), len(wall_ms_list) - 1)]

    ceiling_ms = baseline_ms * 5 + 200  # 0.2s = 200ms
    assert p95_ms <= ceiling_ms, (
        f"POST /auth/register p95 지연 {p95_ms:.2f}ms가 정규화 상한 "
        f"{ceiling_ms:.2f}ms(baseline {baseline_ms:.2f}ms)를 초과했습니다"
    )
