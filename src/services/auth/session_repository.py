"""auth_session CRUD — refresh 회전·재사용 감지·revoke.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M3 DDL, §3.4, §9 PLT-23.

`rotate_refresh()`는 105번 표준의 `conditional_update()`(`expected_state_column`)를
그대로 쓴다 — 호출자가 마지막으로 읽은(=클라이언트가 보낸) refresh 평문의 해시를
`expected_hash`로 넘기면, 그 사이 다른 요청이 이미 회전시켰을 경우 0행이 RETURNING
되어 `ConcurrencyConflictError`가 난다. 이 리프에서는 그 신호를 "동시성 충돌"이
아니라 "탈취된 refresh 토큰의 재사용"으로 해석해 세션을 즉시 revoke한다(§3.4).

task-9420 F4 audit fix — the WHERE now also checks `revoked_at IS NULL` (active)
and `expires_at > now()` (not expired), not just `refresh_hash` equality. If a
logout (revoke) on another connection completes, or the absolute expiry passes,
after `get_active()` read the session but before this rotation UPDATE runs, the
hash can still match yet the row no longer satisfies the condition, so 0 rows
come back RETURNING and rotation is rejected. That race has a different root
cause than "stolen token replay" (the session owner may simply have logged out
elsewhere), so it is surfaced as `RefreshSessionRevokedError` /
`RefreshSessionExpiredMidRotationError` instead of being folded into
`RefreshReuseDetected`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.core.logging.audit_log import record_audit_log
from src.services.auth.tokens import AuthLevel

logger = logging.getLogger(__name__)


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


class PrincipalMismatchError(Exception):
    """Signature-valid access token whose JWT claims (`sub`/`tid`/`auth_level`)
    don't match the actual owner/tenant/auth level of the session `sid` points
    to (F3, AUDIT_2026-09-30 §auth_rls).

    `get_active()` only checks whether `sid` is active, so a "mixed" token —
    session A's `sid` combined with user B's `sub`/`tid`, signed by someone who
    holds a valid signing key — passes the session lookup unchanged. This is
    not signature forgery, but a session-ownership contract gap. The caller
    (`deps.py` `get_current_user`) should map this the same as
    `SessionRevokedError` (401 AUTH_SESSION_REVOKED) and require re-login."""


class RefreshSessionRevokedError(Exception):
    """task-9420 F4 — another connection's logout (revoke) landed between the
    `get_active()` read and this rotation UPDATE. `refresh_hash` itself can
    still match, so the root cause differs from `RefreshReuseDetected` (token
    theft suspicion) — the session was already revoked by that other request,
    so we do not revoke it again here (to avoid overwriting its original
    `revoke_reason`; `revoke()` is idempotent and safe to re-call, but
    preserving the reason means skipping the re-call)."""


class RefreshSessionExpiredMidRotationError(Exception):
    """task-9420 F4 — the absolute expiry (`expires_at`) passed between the
    `get_active()` read and this rotation UPDATE. Distinguished from
    `RefreshReuseDetected` for the same reason, and the session is revoked
    with reason="expired" (matching §3.4's normal expiry handling)."""


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


def verify_principal_binding(
    session: Session, *, user_id: UUID, tenant_id: UUID, auth_level: AuthLevel
) -> None:
    """F3(AUDIT_2026-09-30) — Checks that `session` actually matches the JWT
    claims' `sub`/`tid`/`auth_level`. A legitimate tenant switch (membership-
    based tenant selection; pre-PLT-26 always has `tenant_id == user_id`)
    always matches, because every login/refresh issues a fresh access token
    straight from the session row itself — a mismatch can only happen for a
    token signed with claims that don't belong to the session's owner."""
    if (
        session.user_id != user_id
        or session.tenant_id != tenant_id
        or session.auth_level != auth_level
    ):
        raise PrincipalMismatchError(
            f"session_id={session.id}: claims(sub={user_id}, tid={tenant_id}, "
            f"auth_level={auth_level})가 세션 소유자와 일치하지 않습니다"
        )


async def _record_reuse_detected_audit(
    conn: asyncpg.Connection, session_id: UUID, current: asyncpg.Record | None
) -> None:
    """Records the named audit event (`auth.refresh_reuse_detected`) required by
    L4 §3.4 — best-effort. The session is already revoked by the time this
    runs, so a backend write failure here (WORM outage, etc.) must not undo
    the revoke or block `RefreshReuseDetected` propagation (CLAUDE.md
    fail-closed: revoke first, audit is best-effort). `refresh_hash`/plaintext
    tokens never go into `decision_data` — only `session_id` identifies the
    target."""
    try:
        await record_audit_log(
            conn,
            actor_agent=str(current["user_id"]) if current is not None else "unknown",
            action_type="auth.refresh_reuse_detected",
            user_id=current["user_id"] if current is not None else None,
            target_type="auth_session",
            target_id=str(session_id),
            decision_data={
                "reason": "refresh_reuse",
                "tenant_id": str(current["tenant_id"]) if current is not None else None,
            },
        )
    except Exception:  # noqa: BLE001 — best-effort, revoke already completed
        logger.exception(
            "session_id=%s: failed to record auth.refresh_reuse_detected audit "
            "event (revoke already completed, not re-raising)",
            session_id,
        )


async def rotate_refresh(
    conn: asyncpg.Connection,
    session_id: UUID,
    *,
    expected_hash: str,
    new_hash: str,
) -> Session:
    now = datetime.now(timezone.utc)
    try:
        row = await conditional_update(
            conn,
            table="auth_session",
            id_column="id",
            id_value=session_id,
            expected_state_column="refresh_hash",
            expected_state_value=expected_hash,
            set_values={"refresh_hash": new_hash, "rotated_at": now},
            returning="*",
            extra_conditions={"revoked_at": None},
            extra_gt_conditions={"expires_at": now},
        )
    except ConcurrencyConflictError as exc:
        current = await conn.fetchrow(
            "SELECT user_id, tenant_id, revoked_at, expires_at FROM auth_session WHERE id = $1",
            session_id,
        )
        if current is not None and current["revoked_at"] is not None:
            raise RefreshSessionRevokedError(
                f"session_id={session_id}: 회전 시도 중 다른 요청이 먼저 세션을 revoke했습니다"
            ) from exc
        if current is not None and current["expires_at"] <= now:
            await revoke(conn, session_id, reason="expired")
            raise RefreshSessionExpiredMidRotationError(
                f"session_id={session_id}: 회전 시도 중 절대 만료 시각을 넘겼습니다"
            ) from exc
        await revoke(conn, session_id, reason="refresh_reuse")
        await _record_reuse_detected_audit(conn, session_id, current)
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
