"""task-2020 — PLT-26/PLT-28 원자성 적대적 테스트. task-4152 DEEPEN(D2 보강).

`AuthService.signup`은 `users` 행과 그 PERSONAL `tenant` 행(id == user_id)을
같은 트랜잭션에 넣는다(auth_service.py). 이 테스트는 두 번째 INSERT(tenant)가
실패하면 첫 번째 INSERT(users)도 롤백되는지 — 즉 정말 "같은 트랜잭션"인지를
증명한다.

`users.user_id`는 서버가 임의로 생성하므로(과거엔 `DEFAULT gen_random_uuid()`
의존, 지금은 `AuthService.signup`이 `uuid4()`로 미리 뽑아 명시적으로 INSERT)
가입 전에 그 값을 미리 알 수 없다 — 그래서 `uuid4`를 monkeypatch해 값을
고정하고, 그 id로 `tenant` 행을 먼저 선점해 충돌을 강제한다(이미 이 id를
쓰는 다른 프로세스/오염된 상태를 흉내).

DEEPEN(task-4152) 메모 — D2 하한(ADR-2026-09-09-C Decision 1) 대조:
- negative ≥3: 아래 세 테스트(tenant 충돌 롤백/중복 이메일/약한 비밀번호).
- 실패주입 1: `test_signup_rolls_back_user_row_on_unexpected_tenant_insert_failure`
  — UniqueViolationError가 아닌 임의 예외로도 원자성이 예외 타입에 의존하지
  않음을 증명한다.
- 성능 단언 1: `test_signup_p95_latency_within_budget`. 이 leaf(PLT-26/28,
  비-실행축)는 ADR-2026-09-09-C Decision 1 예산표(사전거래 게이트/주문
  ACK/캔들 조회 등)에 전용 행이 없다 — 회귀 감지용 넉넉한 예산을 건다.
- 게이트 적색 재현 1: 이 파일의 `test_signup_rolls_back_user_row_when_tenant_insert_conflicts`
  자체가 task-2020이 고치기 전 상태(같은 트랜잭션이 아니었을 때)를 재현한다 —
  `signup`에서 `async with ... conn.transaction()`을 벗겨 users/tenant INSERT를
  분리하면 이 테스트가 적색(orphan users 행 생존)으로 재현된다.
"""

from __future__ import annotations

import time
import uuid
from uuid import UUID

import asyncpg
import pytest

from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.domain.models import TenantKind
from src.services import auth_service as auth_service_module
from src.services.auth_service import AuthError, AuthService

JWT_SECRET = "test-secret-key-not-for-production"
STRONG_PASSWORD = "Str0ng!Passw0rd"


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def auth(pool):
    return AuthService(pool, jwt_secret_key=JWT_SECRET)


async def test_signup_rolls_back_user_row_when_tenant_insert_conflicts(auth, pool, monkeypatch):
    fixed_user_id = uuid.uuid4()
    monkeypatch.setattr(auth_service_module, "uuid4", lambda: fixed_user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenant (id, kind) VALUES ($1, $2)",
            fixed_user_id,
            TenantKind.PERSONAL.value,
        )

    email = _unique_email()
    with pytest.raises(asyncpg.UniqueViolationError):
        await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    assert count == 0


async def test_signup_creates_matching_user_and_tenant_id(auth, pool):
    """정상 경로 대조군 — 충돌이 없으면 users.user_id == tenant.id로 둘 다 남는다."""
    email = _unique_email()
    user = await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        tenant_id: UUID | None = await conn.fetchval(
            "SELECT id FROM tenant WHERE id = $1 AND kind = 'PERSONAL'", user.user_id
        )
        user_count = await conn.fetchval(
            "SELECT count(*) FROM users WHERE user_id = $1", user.user_id
        )
    assert tenant_id == user.user_id
    assert user_count == 1


async def test_signup_rejects_duplicate_email_and_leaves_single_tenant_row(auth, pool):
    """negative — 중복 이메일 두 번째 시도는 트랜잭션 앞단(existing 체크)에서
    거부되어 tenant INSERT까지 도달하지 않는다. 고아 tenant 행이 생기지 않음을
    함께 증명한다."""
    email = _unique_email()
    first = await auth.signup(email, STRONG_PASSWORD)

    with pytest.raises(AuthError, match="이미 등록된 이메일"):
        await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        user_count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
        tenant_count = await conn.fetchval(
            "SELECT count(*) FROM tenant WHERE id = $1", first.user_id
        )
    assert user_count == 1
    assert tenant_count == 1


async def test_signup_rejects_weak_password_without_creating_any_row(auth, pool):
    """negative — 강도 미달 비밀번호는 DB 트랜잭션이 열리기 전에 거부되어
    users/tenant 어느 쪽에도 행이 남지 않는다."""
    email = _unique_email()
    with pytest.raises(AuthError, match="비밀번호는 최소"):
        await auth.signup(email, "weak")

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    assert count == 0


async def test_signup_rolls_back_user_row_on_unexpected_tenant_insert_failure(
    auth, pool, monkeypatch
):
    """실패주입 — UniqueViolationError 한 종류만으로는 "예외 종류에 상관없이
    같은 트랜잭션"이라는 주장을 다 증명하지 못한다. tenant INSERT 단계에서
    전혀 다른 예외(백엔드 회귀를 흉내낸 RuntimeError)를 주입해도 users 행이
    롤백되는지 확인한다."""

    async def _boom(
        self: PostgresMembershipRepository,
        conn: asyncpg.Connection,
        *,
        tenant_id,
        kind,
        display_name=None,
    ):
        raise RuntimeError("simulated backend regression in insert_tenant")

    monkeypatch.setattr(PostgresMembershipRepository, "insert_tenant", _boom)

    email = _unique_email()
    with pytest.raises(RuntimeError):
        await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email)
    assert count == 0


@pytest.mark.perf
async def test_signup_p95_latency_within_budget(auth, pool):
    """성능 단언(D2) — 이 leaf(PLT-26/28)는 ADR-2026-09-09-C Decision 1
    예산표에 전용 행이 없는 비-실행축이다. argon2 해시 + 로컬 DB 왕복을 포함해도
    넉넉히 넘지 말아야 할 회귀 감지용 예산(호출당 1.5초)을 건다."""
    durations: list[float] = []
    for _ in range(5):
        email = _unique_email()
        start = time.perf_counter()
        await auth.signup(email, STRONG_PASSWORD)
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[-1]
    assert p95 < 1.5, f"signup p95 latency {p95:.3f}s exceeded 1.5s budget"
