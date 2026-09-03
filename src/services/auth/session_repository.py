"""auth_session CRUD — refresh 회전·재사용 감지·revoke.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M3 DDL, §3.4, §9 PLT-23.

`rotate_refresh()`는 105번 표준의 `conditional_update()`(`expected_state_column`)를
그대로 쓴다 — 호출자가 마지막으로 읽은(=클라이언트가 보낸) refresh 평문의 해시를
`expected_hash`로 넘기면, 그 사이 다른 요청이 이미 회전시켰을 경우 0행이 RETURNING
되어 `ConcurrencyConflictError`가 난다. 이 리프에서는 그 신호를 "동시성 충돌"이
아니라 "탈취된 refresh 토큰의 재사용"으로 해석해 세션을 즉시 revoke한다(§3.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.services.auth.tokens import AuthLevel


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
    tenant_id: UUID
    refresh_hash: str
    auth_level: AuthLevel
    issued_at: datetime
    rotated_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


class RefreshReuseDetected(Exception):
    """이미 회전되었거나 존재하지 않는 refresh_hash 재사용 시도.

    호출자(§9 PLT-24 `refresh.py`)는 이 예외를 401로 매핑하고 재로그인을
    요구해야 한다 — 세션은 이미 이 함수 안에서 revoke됐다."""


def _row_to_session(row: asyncpg.Record) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        tenant_id=row["tenant_id"],
        refresh_hash=row["refresh_hash"],
        auth_level=row["auth_level"],
        issued_at=row["issued_at"],
        rotated_at=row["rotated_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        revoke_reason=row["revoke_reason"],
    )


async def insert_session(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    refresh_hash: str,
    ip_hash: str | None,
    ua_hash: str | None,
    expires_at: datetime,
    auth_level: AuthLevel = "PASSWORD",
) -> Session:
    row = await conn.fetchrow(
        "INSERT INTO auth_session "
        "(user_id, tenant_id, refresh_hash, auth_level, ip_hash, ua_hash, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
        user_id,
        tenant_id,
        refresh_hash,
        auth_level,
        ip_hash,
        ua_hash,
        expires_at,
    )
    assert row is not None
    return _row_to_session(row)


async def get_active(conn: asyncpg.Connection, session_id: UUID) -> Session | None:
    row = await conn.fetchrow(
        "SELECT * FROM auth_session WHERE id = $1 AND revoked_at IS NULL", session_id
    )
    return None if row is None else _row_to_session(row)


async def rotate_refresh(
    conn: asyncpg.Connection,
    session_id: UUID,
    *,
    expected_hash: str,
    new_hash: str,
) -> Session:
    try:
        row = await conditional_update(
            conn,
            table="auth_session",
            id_column="id",
            id_value=session_id,
            expected_state_column="refresh_hash",
            expected_state_value=expected_hash,
            set_values={"refresh_hash": new_hash, "rotated_at": datetime.now(timezone.utc)},
            returning="*",
        )
    except ConcurrencyConflictError as exc:
        await revoke(conn, session_id, reason="refresh_reuse")
        raise RefreshReuseDetected(
            f"session_id={session_id}: refresh_hash 재사용 감지 — 세션을 revoke했습니다"
        ) from exc
    return _row_to_session(row)


async def revoke(conn: asyncpg.Connection, session_id: UUID, *, reason: str) -> None:
    """`revoked_at IS NULL`인 행만 갱신 — 이미 revoked면 조용히 no-op(멱등, §3.4:
    "0행이면 이미 revoked, 에러 아님")."""
    await conn.execute(
        "UPDATE auth_session SET revoked_at = now(), revoke_reason = $2 "
        "WHERE id = $1 AND revoked_at IS NULL",
        session_id,
        reason,
    )


async def revoke_all_for_user(conn: asyncpg.Connection, user_id: UUID, *, reason: str) -> int:
    result = await conn.execute(
        "UPDATE auth_session SET revoked_at = now(), revoke_reason = $2 "
        "WHERE user_id = $1 AND revoked_at IS NULL",
        user_id,
        reason,
    )
    return int(result.split()[-1])
