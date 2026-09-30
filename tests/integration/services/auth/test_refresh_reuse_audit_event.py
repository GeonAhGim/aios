"""task-9452 F5(L) — refresh 재사용 감지 감사 이벤트(`auth.refresh_reuse_detected`).

Spec: docs/audits/AUDIT_2026-09-30_auth_rls.md F5, docs/specs/
L4_platform_observability_tenancy_api_v1.0.md §3.4.

`session_repository.rotate_refresh()`의 재사용 감지 분기는 이미 `revoke_reason=
'refresh_reuse'` 갱신과 `RefreshReuseDetected` 예외를 던졌지만, L4 §3.4가 요구하는
명명된 감사 이벤트 기록이 없었다(정적 발견, task-9420). 여기서는 그 감사 이벤트
자체를 왕복 검증한다 — `test_refresh_reuse_of_old_refresh_token_revokes_session`
류의 기존 테스트는 revoke/예외만 다루고 audit_log는 보지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from unittest import mock

import asyncpg
import pytest

from src.services.auth import login as login_usecase
from src.services.auth import refresh as refresh_usecase
from src.services.auth import session_repository
from src.services.auth.tokens import TokenIssuer
from src.services.auth_service import AuthService

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


def _auth(pool: asyncpg.Pool) -> AuthService:
    return AuthService(pool, jwt_secret_key="unused-legacy-secret-min-32-bytes-long")


def _issuer() -> TokenIssuer:
    return TokenIssuer.from_env()


def _unique_email() -> str:
    return f"test-refresh-reuse-audit-{uuid.uuid4().hex}@example.com"


async def _signup_and_login(pool: asyncpg.Pool):
    auth = _auth(pool)
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)
    return await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)


async def _reuse_events(pool: asyncpg.Pool, session_id: uuid.UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, actor_agent, action_type, target_id, decision_data "
            "FROM audit_log WHERE action_type = 'auth.refresh_reuse_detected' "
            "AND target_id = $1",
            str(session_id),
        )


# ---------------------------------------------------------------------------
# Positive — event is recorded with actor/session/tenant/reason
# ---------------------------------------------------------------------------


async def test_refresh_reuse_records_named_audit_event(pool):
    pair = await _signup_and_login(pool)
    await refresh_usecase.refresh(
        pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
    )

    with pytest.raises(session_repository.RefreshReuseDetected):
        await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
        )

    events = await _reuse_events(pool, pair.session_id)
    assert len(events) == 1
    event = events[0]
    assert event["user_id"] is not None
    assert event["actor_agent"] == str(event["user_id"])
    assert event["target_id"] == str(pair.session_id)
    decision_data = json.loads(event["decision_data"])
    assert decision_data["reason"] == "refresh_reuse"
    assert decision_data["tenant_id"] is not None


# ---------------------------------------------------------------------------
# Negative — no plaintext/hash leakage into the audit row
# ---------------------------------------------------------------------------


async def test_refresh_reuse_audit_event_excludes_token_plaintext_and_hash(pool):
    pair = await _signup_and_login(pool)
    old_refresh_plaintext = pair.refresh_token
    await refresh_usecase.refresh(
        pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
    )

    with pytest.raises(session_repository.RefreshReuseDetected):
        await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=old_refresh_plaintext
        )

    events = await _reuse_events(pool, pair.session_id)
    assert len(events) == 1
    raw_row = json.dumps(dict(events[0]), default=str)
    assert old_refresh_plaintext not in raw_row, "평문 refresh 토큰이 감사 행에 남으면 안 된다"

    async with pool.acquire() as conn:
        session_row = await conn.fetchrow(
            "SELECT refresh_hash FROM auth_session WHERE id = $1", pair.session_id
        )
    assert session_row["refresh_hash"] not in raw_row, "refresh_hash도 감사 행에 남으면 안 된다"


# ---------------------------------------------------------------------------
# Failure injection — audit backend failure must not block session revoke
# ---------------------------------------------------------------------------


async def test_refresh_reuse_revokes_session_even_if_audit_backend_write_fails(pool):
    """실패 주입: `record_audit_log`가 예외를 던져도(WORM 백엔드 장애 등) 세션
    revoke는 이미 그 전에 완료돼 있으므로 그대로 유지돼야 한다(fail-closed
    우선순위 — 폐기가 감사보다 우선)."""
    pair = await _signup_and_login(pool)
    await refresh_usecase.refresh(
        pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit backend failure")

    with mock.patch.object(session_repository, "record_audit_log", side_effect=_boom):
        with pytest.raises(session_repository.RefreshReuseDetected):
            await refresh_usecase.refresh(
                pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
            )

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is None, "감사 기록 실패와 무관하게 세션은 revoke된 상태로 유지돼야 한다"

    # 실패 주입 동안은 이벤트가 기록되지 않았어야 한다(삼켜진 예외가 거짓 성공을
    # 위장하지 않는지 확인).
    events = await _reuse_events(pool, pair.session_id)
    assert events == []


# ---------------------------------------------------------------------------
# Concurrent reuse — event-count contract
# ---------------------------------------------------------------------------


async def test_concurrent_refresh_reuse_records_exactly_one_audit_event(pool):
    """동시 재사용 시 이벤트 수 계약 — 같은 refresh_token으로 두 요청이 동시에
    경합하면 정확히 한쪽만 `ConcurrencyConflictError`를 거쳐 reuse 분기를 타므로,
    감사 이벤트도 정확히 1건만 기록돼야 한다(승자는 이벤트를 만들지 않는다)."""
    pair = await _signup_and_login(pool)

    async def attempt():
        return await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], session_repository.RefreshReuseDetected)

    events = await _reuse_events(pool, pair.session_id)
    assert len(events) == 1
