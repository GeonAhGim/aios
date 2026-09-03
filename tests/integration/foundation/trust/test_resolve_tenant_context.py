"""resolve_tenant_context 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-28.
DoD(task-1090): `X-Tenant-Id` 미지정 -> personal 테넌트; 활성 멤버십 없는
tenant 요청(비회원) -> `TenantMismatchError`(403 AUTH_TENANT_MISMATCH);
기존 v1 계약(`TenantContext`) 생성 방식이 무수정으로 계속 통과한다.
"""
from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.application.resolve_tenant_context import (
    TenantMismatchError,
    resolve_tenant_context,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import MembershipRole, TenantKind
from src.services.auth_service import User
from tests.integration.conftest import create_test_user


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
