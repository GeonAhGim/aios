"""resolve_tenant_context 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-28.
DoD(task-1090): `X-Tenant-Id` 미지정 -> personal 테넌트; 활성 멤버십 없는
tenant 요청(비회원) -> `TenantMismatchError`(403 AUTH_TENANT_MISMATCH);
기존 v1 계약(`TenantContext`) 생성 방식이 무수정으로 계속 통과한다.
"""

from __future__ import annotations

import os
import time
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from src.api.foundation_deps import get_tenant_context
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.application.resolve_tenant_context import (
    TenantMismatchError,
    resolve_tenant_context,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import (
    Membership,
    MembershipRole,
    MembershipState,
    Tenant,
    TenantKind,
)
from src.services.auth_service import User
from tests.integration.conftest import create_test_user

# ADR-2026-09-09-C §"축별 성능 예산" — resolve_tenant_context 전용 항목은 없으므로,
# 가장 근접한 DB 읽기 비교축(tenant_membership INSERT p95 50ms, task-3159가 차용한
# 것과 동일 예산)을 조회 경로에도 그대로 차용한다.
RESOLVE_TENANT_CONTEXT_P95_BUDGET_MS = 50.0


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMembershipRepository(pool)


def _user(user_id) -> User:
    return User(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


async def test_no_requested_tenant_returns_personal_context(pool, repo):
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        context = await resolve_tenant_context(
            repo, conn, user=_user(user_id), requested_tenant_id=None, mfa_verified=False
        )

    assert context.tenant_id == user_id
    assert context.subject_id == user_id
    assert context.role == "OWNER"
    assert context.membership_id is None


async def test_requested_tenant_with_active_membership_returns_membership_role(pool, repo):
    owner_id = await create_test_user(pool)
    member_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=owner_id, kind=TenantKind.HOUSEHOLD)
        membership = await repo.insert_membership(
            conn,
            tenant_id=owner_id,
            subject_id=member_id,
            role=MembershipRole.ADMIN,
            created_by=owner_id,
        )

        context = await resolve_tenant_context(
            repo,
            conn,
            user=_user(member_id),
            requested_tenant_id=owner_id,
            mfa_verified=True,
        )

    assert context.tenant_id == owner_id
    assert context.subject_id == member_id
    assert context.role == "ADMIN"
    assert context.membership_id == membership.id
    assert context.mfa_verified is True


async def test_requested_tenant_without_membership_raises_tenant_mismatch(pool, repo):
    """DoD negative: 비회원(활성 멤버십 없음)이 다른 tenant를 요청하면
    `TenantMismatchError` -> 호출부가 403 AUTH_TENANT_MISMATCH로 번역."""
    stranger_id = await create_test_user(pool)
    other_tenant_id = uuid4()

    async with pool.acquire() as conn:
        with pytest.raises(TenantMismatchError):
            await resolve_tenant_context(
                repo,
                conn,
                user=_user(stranger_id),
                requested_tenant_id=other_tenant_id,
                mfa_verified=False,
            )


async def test_suspended_membership_raises_tenant_mismatch(pool, repo):
    """negative(축 2): 한때 ACTIVE였던 멤버십이 SUSPENDED로 전이되면
    `get_active_membership`이 동형으로 `None`을 돌려주고(§8.3), 완전한
    비회원과 마찬가지로 `TenantMismatchError`가 발생한다 — "멤버십 행이
    존재한다"와 "지금 활성 권한이 있다"를 구분하지 못하는 회귀를 잡는다."""
    owner_id = await create_test_user(pool)
    member_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=owner_id, kind=TenantKind.HOUSEHOLD)
        membership = await repo.insert_membership(
            conn,
            tenant_id=owner_id,
            subject_id=member_id,
            role=MembershipRole.MEMBER,
            created_by=owner_id,
        )
        await repo.update_conditional_membership_state(
            conn,
            membership.id,
            owner_id,
            expected_state=MembershipState.ACTIVE,
            expected_revision=membership.revision,
            new_state=MembershipState.SUSPENDED,
        )

        with pytest.raises(TenantMismatchError):
            await resolve_tenant_context(
                repo,
                conn,
                user=_user(member_id),
                requested_tenant_id=owner_id,
                mfa_verified=False,
            )


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


async def test_malformed_tenant_header_raises_400_validation_error(pool, repo):
    """negative: `X-Tenant-Id`가 UUID 형식이 아니면 `resolve_tenant_context`
    (DB 조회)에 도달하기 전에 호출부(`foundation_deps.get_tenant_context`)가
    400 VALIDATION_INVALID_FIELD로 거부한다."""
    user_id = await create_test_user(pool)

    with pytest.raises(HTTPException) as excinfo:
        await get_tenant_context(
            _FakeRequest({"X-Tenant-Id": "not-a-uuid"}),  # type: ignore[arg-type]
            user=_user(user_id),
            pool=pool,
            membership_repo=repo,
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error_code"] == "VALIDATION_INVALID_FIELD"


class _UnusedFakeMethodError(AssertionError):
    """테스트 fake의 미사용 프로토콜 메서드가 실수로 호출됐을 때만 발생 —
    `NotImplementedError`(ratchet-2286 대상, 실서비스 어댑터 fail-closed
    스텁 전용)와 성격이 다르므로 별도 예외로 구분한다."""


class _OutageMembershipRepo:
    """실패 주입용 fake — `MembershipRepository` 프로토콜 전체를 구현해
    `resolve_tenant_context`에 타입 그대로 넘길 수 있게 한다.
    `get_active_membership`만 DB 커넥션 단절을 흉내내는 예외를 던지고,
    나머지는 이 테스트 경로에서 호출되지 않는다."""

    async def get_active_membership(
        self, conn: asyncpg.Connection, tenant_id: UUID, subject_id: UUID
    ) -> Membership | None:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated pool outage")

    async def list_memberships_for_subject(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> list[Membership]:
        raise _UnusedFakeMethodError

    async def count_active_owners(self, conn: asyncpg.Connection, tenant_id: UUID) -> int:
        raise _UnusedFakeMethodError

    async def insert_membership(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        role: MembershipRole,
        created_by: UUID,
    ) -> Membership:
        raise _UnusedFakeMethodError

    async def update_conditional_membership_state(
        self,
        conn: asyncpg.Connection,
        membership_id: UUID,
        tenant_id: UUID,
        *,
        expected_state: MembershipState,
        expected_revision: int,
        new_state: MembershipState,
    ) -> Membership:
        raise _UnusedFakeMethodError

    async def get_personal_tenant(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> Tenant | None:
        raise _UnusedFakeMethodError

    async def insert_tenant(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        kind: TenantKind,
        display_name: str | None = None,
    ) -> Tenant:
        raise _UnusedFakeMethodError


async def test_membership_lookup_outage_propagates_fail_closed(pool):
    """실패 주입: 멤버십 조회 도중 DB 커넥션이 끊기면(회귀 상상: outage를
    삼키고 personal/OWNER 컨텍스트로 조용히 폴백) `resolve_tenant_context`가
    예외를 그대로 전파해야 한다 — CLAUDE.md §3 "기본 태세는 fail-closed".
    비회원에게 잘못된 접근 권한을 발급하는 것보다 요청 실패가 안전하다."""
    user_id = await create_test_user(pool)
    other_tenant_id = uuid4()
    outage_repo = _OutageMembershipRepo()

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await resolve_tenant_context(
                outage_repo,
                conn,
                user=_user(user_id),
                requested_tenant_id=other_tenant_id,
                mfa_verified=False,
            )


# ----------------------------------------------------------------------
# 성능 단언 — 활성 멤버십 조회 경로 p95
# ----------------------------------------------------------------------


async def test_resolve_tenant_context_membership_path_latency_p95_within_budget(pool, repo):
    """수치 성능 단언: 멤버십이 있는 tenant 요청 경로(DB 조회 1회, §8.3)의
    p95 지연시간이 예산을 넘지 않아야 한다 — 인덱스/쿼리플랜 회귀로 조회가
    느려지는 것을 잡기 위함이지 현재 실측치에 딱 맞춘 문턱은 아니다."""
    owner_id = await create_test_user(pool)
    member_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=owner_id, kind=TenantKind.ORGANIZATION)
        await repo.insert_membership(
            conn,
            tenant_id=owner_id,
            subject_id=member_id,
            role=MembershipRole.MEMBER,
            created_by=owner_id,
        )

    n = 30
    latencies: list[float] = []
    for _ in range(n):
        async with pool.acquire() as conn:
            start = time.perf_counter()
            await resolve_tenant_context(
                repo,
                conn,
                user=_user(member_id),
                requested_tenant_id=owner_id,
                mfa_verified=False,
            )
            latencies.append(time.perf_counter() - start)

    latencies.sort()
    p95_ms = latencies[int(n * 0.95)] * 1000
    assert p95_ms < RESOLVE_TENANT_CONTEXT_P95_BUDGET_MS, (
        f"resolve_tenant_context(멤버십 경로) p95 지연시간 {p95_ms:.4f}ms가 예산 "
        f"{RESOLVE_TENANT_CONTEXT_P95_BUDGET_MS}ms 초과"
    )


# ----------------------------------------------------------------------
# 게이트 적색 재현 — 멤버십 None 체크가 빠지면 비회원에게 접근 권한이 샌다
# ----------------------------------------------------------------------


async def test_gate_red_reproduction_without_membership_none_check(pool, repo):
    """게이트 적색 재현 — `resolve_tenant_context`의 `if membership is None:
    raise TenantMismatchError(...)` 분기를 지운 회귀본(그럴듯한 "단순화":
    membership이 없으면 그냥 OWNER personal-ish context를 만들자)을 이
    테스트 안에서 직접 재현하면, 비회원이 실제로 다른 tenant의 컨텍스트를
    발급받는다(적색 상태) — 즉 `test_requested_tenant_without_membership_
    raises_tenant_mismatch`가 이 회귀를 실제로 잡아낸다는 것을 증명한다."""
    stranger_id = await create_test_user(pool)
    other_tenant_id = uuid4()

    async def _regressed_resolve_tenant_context(
        repo, conn, *, user, requested_tenant_id, mfa_verified
    ):  # noqa: ANN001
        membership = await repo.get_active_membership(conn, requested_tenant_id, user.user_id)
        # 회귀: `if membership is None: raise TenantMismatchError(...)`가 빠짐.
        return TenantContext(
            tenant_id=requested_tenant_id,
            subject_id=user.user_id,
            role=membership.role.value if membership else "OWNER",
            mfa_verified=mfa_verified,
            membership_id=membership.id if membership else None,
        )

    async with pool.acquire() as conn:
        leaked_context = await _regressed_resolve_tenant_context(
            repo,
            conn,
            user=_user(stranger_id),
            requested_tenant_id=other_tenant_id,
            mfa_verified=False,
        )
        assert leaked_context.tenant_id == other_tenant_id  # 회귀 재현: 접근 권한이 샌다

        # 실제 구현은 같은 입력에 대해 fail-closed로 거부한다 — 이 가드가
        # 실재하고 위 회귀를 잡아낼 수 있음을 대조 증명.
        with pytest.raises(TenantMismatchError):
            await resolve_tenant_context(
                repo,
                conn,
                user=_user(stranger_id),
                requested_tenant_id=other_tenant_id,
                mfa_verified=False,
            )


def test_existing_v1_fixture_parsing_unmodified() -> None:
    """DoD: 기존 v1 fixture 파싱(키워드 인자만으로 생성)이 새 optional
    `membership_id` 필드 추가 후에도 무수정으로 통과한다."""
    user_id = uuid4()
    context = TenantContext(tenant_id=user_id, subject_id=user_id, role="OWNER", mfa_verified=False)

    assert context.membership_id is None
    assert context.schema_version == "v1"

    parsed = TenantContext.model_validate(
        {
            "tenant_id": str(user_id),
            "subject_id": str(user_id),
            "role": "OWNER",
            "mfa_verified": False,
        }
    )
    assert parsed.membership_id is None
    assert isinstance(parsed.tenant_id, type(user_id))
