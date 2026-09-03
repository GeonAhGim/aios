"""session_repository.py 통합테스트(TEST_DATABASE_URL) — 핵심 DoD:
refresh 회전 후 이전 해시 재사용 감지 시 세션 revoke.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M3, §3.4, §9 PLT-23.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.services.auth import session_repository as sessions
from src.services.auth.tokens import TokenIssuer
from tests.integration.conftest import create_test_user


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=14)


async def _insert_session(pool, user_id):
    _plaintext, refresh_hash = TokenIssuer.issue_refresh()
    async with pool.acquire() as conn:
        return await sessions.insert_session(
            conn,
            user_id=user_id,
            tenant_id=user_id,
            refresh_hash=refresh_hash,
            ip_hash=None,
            ua_hash=None,
            expires_at=_expires_at(),
        )


async def test_insert_and_get_active_roundtrip(pool):
    user_id = await create_test_user(pool)
    session = await _insert_session(pool, user_id)

    async with pool.acquire() as conn:
        fetched = await sessions.get_active(conn, session.id)

    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.user_id == user_id
    assert fetched.revoked_at is None
    assert fetched.auth_level == "PASSWORD"


async def test_get_active_returns_none_for_unknown_session(pool):
    async with pool.acquire() as conn:
        assert await sessions.get_active(conn, uuid4()) is None


async def test_rotate_refresh_with_expected_hash_succeeds(pool):
    user_id = await create_test_user(pool)
    session = await _insert_session(pool, user_id)
    _new_plaintext, new_hash = TokenIssuer.issue_refresh()

    async with pool.acquire() as conn:
        rotated = await sessions.rotate_refresh(
            conn, session.id, expected_hash=session.refresh_hash, new_hash=new_hash
        )

    assert rotated.refresh_hash == new_hash
    assert rotated.rotated_at is not None
    assert rotated.revoked_at is None


async def test_rotate_refresh_reuse_of_old_hash_revokes_session(pool):
    """핵심 DoD: 이미 회전된(옛) refresh_hash로 다시 rotate를 시도하면
    RefreshReuseDetected가 나고, 세션은 즉시 revoke돼야 한다."""
    user_id = await create_test_user(pool)
    session = await _insert_session(pool, user_id)
    old_hash = session.refresh_hash
    _second_plaintext, second_hash = TokenIssuer.issue_refresh()

    async with pool.acquire() as conn:
        await sessions.rotate_refresh(
            conn, session.id, expected_hash=old_hash, new_hash=second_hash
        )

        # 공격자(또는 재시도 클라이언트)가 옛 refresh_hash로 다시 회전을 시도
        with pytest.raises(sessions.RefreshReuseDetected):
            await sessions.rotate_refresh(
                conn, session.id, expected_hash=old_hash, new_hash="cc" * 32
            )

        revoked = await sessions.get_active(conn, session.id)
        row = await conn.fetchrow(
            "SELECT revoked_at, revoke_reason FROM auth_session WHERE id = $1", session.id
        )

    assert revoked is None  # get_active는 revoked 세션을 반환하지 않는다
    assert row["revoked_at"] is not None
    assert row["revoke_reason"] == "refresh_reuse"


async def test_rotate_refresh_on_unknown_session_raises_reuse_detected(pool):
    async with pool.acquire() as conn:
        with pytest.raises(sessions.RefreshReuseDetected):
            await sessions.rotate_refresh(
                conn, uuid4(), expected_hash="aa" * 32, new_hash="bb" * 32
            )


async def test_revoke_is_idempotent(pool):
    user_id = await create_test_user(pool)
    session = await _insert_session(pool, user_id)

    async with pool.acquire() as conn:
        await sessions.revoke(conn, session.id, reason="logout")
        first_row = await conn.fetchrow(
            "SELECT revoked_at, revoke_reason FROM auth_session WHERE id = $1", session.id
        )
        # 두 번째 revoke는 조건절(WHERE revoked_at IS NULL)에 걸려 no-op — 에러 없음
        await sessions.revoke(conn, session.id, reason="logout_all")
        second_row = await conn.fetchrow(
            "SELECT revoked_at, revoke_reason FROM auth_session WHERE id = $1", session.id
        )

    assert first_row["revoke_reason"] == "logout"
    assert second_row["revoke_reason"] == "logout"  # 두 번째 호출이 덮어쓰지 않음
    assert second_row["revoked_at"] == first_row["revoked_at"]


async def test_revoke_all_for_user_returns_count_and_revokes_only_active(pool):
    user_id = await create_test_user(pool)
    session_a = await _insert_session(pool, user_id)
    session_b = await _insert_session(pool, user_id)

    async with pool.acquire() as conn:
        await sessions.revoke(conn, session_a.id, reason="logout")
        count = await sessions.revoke_all_for_user(conn, user_id, reason="admin_suspend")

    assert count == 1  # session_a는 이미 revoked라 대상에서 제외

    async with pool.acquire() as conn:
        row_a = await conn.fetchrow(
            "SELECT revoke_reason FROM auth_session WHERE id = $1", session_a.id
        )
        row_b = await conn.fetchrow(
            "SELECT revoke_reason FROM auth_session WHERE id = $1", session_b.id
        )

    assert row_a["revoke_reason"] == "logout"
    assert row_b["revoke_reason"] == "admin_suspend"
