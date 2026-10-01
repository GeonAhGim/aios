"""11.2 — Sign-up/sign-in service (AuthService).

Spec: functional_spec_v1.20.md#FD-11.1, 13_multi_tenancy_auth_v1.4.md#§13.2

No FastAPI router yet (worktree #16, API wiring phase) — as with other
safety/approval services in this session (ApprovalService,
ReconciliationService, etc.), only the pure service layer is implemented now;
the router will call this class directly during the wiring phase.

Deviation: §13.2 users DDL lacks columns for login-failure lockout state,
so we added failed_login_attempts/locked_until (doc v1.4, migration
b2c3d4e5f6a7).

MFA (TOTP) verification is planned for FD-11.2 (worktree 11.3) — not yet
implemented. We inject verify_totp as a DI callback. If an account with
mfa_enabled=True attempts login without a callback, we fail safely.

11.6 integration — successful login while in PENDING_DELETION state
(FD-11.4 withdrawal grace period) automatically cancels the deletion
(returns to ACTIVE, resets deletion_requested_at).

PLT-22 integration — login-failure counter increments are delegated to an
atomic UPDATE in `src/services/auth/lockout.py` (removes TOCTOU, task-852).
When a lock decision fires, we raise `AccountLockedError` (§3.3
AUTH_ACCOUNT_LOCKED · 423 contract) — the router is not yet wired
(§9 PLT-24), so for now the AuthError subclass inherits the existing 401
mapping.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.logging.audit_log import record_audit_log
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.domain.models import TenantKind
from src.services.auth import lockout

MIN_PASSWORD_LENGTH = 12

_GENERIC_AUTH_ERROR = "이메일 또는 비밀번호가 올바르지 않습니다."

_hasher = PasswordHasher()
# Red-team audit (docs/RED_TEAM_FINDINGS.md #12) — if the account-not-found/
# suspended/locked paths skip Argon2 verify() and respond faster, that
# timing difference itself becomes a timing side-channel that reveals account
# existence. Verify a fixed dummy hash on every failure path to normalise
# response time.
_DUMMY_PASSWORD_HASH = _hasher.hash("timing-normalization-dummy-password")

VerifyTotpFn = Callable[[UUID, str, str], Awaitable[bool]]


def _consume_verify_timing(password: str) -> None:
    try:
        _hasher.verify(_DUMMY_PASSWORD_HASH, password)
    except VerifyMismatchError:
        pass


class AuthError(Exception):
    """FD-11.1 Auth/signup failure — router maps to appropriate HTTP status code."""


class AccountLockedError(AuthError):
    """PLT-22 — Login attempt on a locked account. §3.3 AUTH_ACCOUNT_LOCKED(423)
    contract — fixes `error_code`/`retry_after_seconds` names (frontend
    deriveLockout, task-387 reads these names)."""

    error_code = "AUTH_ACCOUNT_LOCKED"
    http_status = 423

    def __init__(self, retry_after_seconds: int | None) -> None:
        super().__init__(_GENERIC_AUTH_ERROR)
        self.retry_after_seconds = retry_after_seconds


class User(BaseModel):
    user_id: UUID
    email: str
    display_name: str | None
    mfa_enabled: bool
    mfa_verified_at: datetime | None
    status: str
    is_verifier: bool
    is_platform_admin: bool


def _password_strong_enough(password: str) -> bool:
    """Draft strength rules (FD-11.1): min 12 chars + upper/lower/digit/special."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    return bool(
        re.search(r"[a-z]", password)
        and re.search(r"[A-Z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^\w\s]", password)
    )


def _row_to_user(row: asyncpg.Record) -> User:
    return User(
        user_id=row["user_id"],
        email=row["email"],
        display_name=row["display_name"],
        mfa_enabled=row["mfa_enabled"],
        mfa_verified_at=row["mfa_verified_at"],
        status=row["status"],
        is_verifier=row["is_verifier"],
        is_platform_admin=row["is_platform_admin"],
    )


async def get_user_by_id(pool: asyncpg.Pool, user_id: UUID) -> User | None:
    """Public lookup used by the app wiring phase (get_current_user JWT
    validation)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    return _row_to_user(row) if row is not None else None


class AuthService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        jwt_secret_key: str,
        jwt_algorithm: str = "HS256",
        jwt_expire_minutes: int = 60,
        verify_totp: VerifyTotpFn | None = None,
    ) -> None:
        self._pool = pool
        self._jwt_secret_key = jwt_secret_key
        self._jwt_algorithm = jwt_algorithm
        self._jwt_expire_minutes = jwt_expire_minutes
        self._verify_totp = verify_totp

    async def signup(self, email: str, password: str) -> User:
        if not _password_strong_enough(password):
            raise AuthError(
                "비밀번호는 최소 12자 이상이어야 하며 대소문자·숫자·특수문자를 포함해야 합니다."
            )

        membership_repo = PostgresMembershipRepository(self._pool)
        user_id = uuid4()
        async with self._pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email)
            if existing is not None:
                raise AuthError("이미 등록된 이메일입니다.")

            password_hash = _hasher.hash(password)
            try:
                row = await conn.fetchrow(
                    "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3) "
                    "RETURNING *",
                    user_id,
                    email,
                    password_hash,
                )
            except asyncpg.UniqueViolationError as exc:
                # TOCTOU (review task-2083 REJECT #1): SELECT above misses a
                # concurrent signup that commits before this INSERT. Only
                # remap the email conflict; re-raise anything else fail-closed.
                if exc.constraint_name != "users_email_key":
                    raise
                raise ConcurrencyConflictError(
                    f"email={email}: concurrent signup won the race."
                ) from exc
            # PLT-26/PLT-28 wiring: the users row and its PERSONAL tenant
            # (id == user_id) go in the same transaction -- foundation writes
            # after signup FK tenant_id -> tenant(id) and need it to exist.
            await membership_repo.insert_tenant(conn, tenant_id=user_id, kind=TenantKind.PERSONAL)
        return _row_to_user(row)

    async def authenticate(
        self, email: str, password: str, *, totp_code: str | None = None
    ) -> User:
        """FD-11.1 All 3 exception cases (account not found / locked /
        SUSPENDED·DELETED) handled here, all forwarded to router with the same
        generic message — prevents account enumeration attacks."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
            if row is None:
                _consume_verify_timing(password)
                # Never log password/TOTP values — only record the attempted email
                # and reason (no user_id exists when the account is absent).
                await record_audit_log(
                    conn,
                    actor_agent="unknown",
                    action_type="auth.login_failed",
                    decision_data={"email": email, "reason": "account_not_found"},
                )
                raise AuthError(_GENERIC_AUTH_ERROR)

            if row["status"] in ("SUSPENDED", "DELETED"):
                _consume_verify_timing(password)
                await record_audit_log(
                    conn,
                    actor_agent=str(row["user_id"]),
                    action_type="auth.login_failed",
                    user_id=row["user_id"],
                    decision_data={
                        "reason": "account_suspended_or_deleted",
                        "status": row["status"],
                    },
                )
                raise AuthError(_GENERIC_AUTH_ERROR)
            now = datetime.now(timezone.utc)
            if row["locked_until"] is not None and now < row["locked_until"]:
                _consume_verify_timing(password)
                await record_audit_log(
                    conn,
                    actor_agent=str(row["user_id"]),
                    action_type="auth.login_failed",
                    user_id=row["user_id"],
                    decision_data={"reason": "account_locked"},
                )
                locked_state = await lockout.register_failed_attempt(conn, row["user_id"], now=now)
                raise AccountLockedError(locked_state.retry_after_seconds)

            try:
                _hasher.verify(row["password_hash"], password)
            except VerifyMismatchError:
                await record_audit_log(
                    conn,
                    actor_agent=str(row["user_id"]),
                    action_type="auth.login_failed",
                    user_id=row["user_id"],
                    decision_data={"reason": "wrong_password"},
                )
                raise await self._fail_login(conn, row["user_id"], now) from None

            totp_verified_now = False
            if row["mfa_enabled"]:
                totp_ok = (
                    totp_code is not None
                    and self._verify_totp is not None
                    and await self._verify_totp(row["user_id"], row["mfa_secret"], totp_code)
                )
                if not totp_ok:
                    await record_audit_log(
                        conn,
                        actor_agent=str(row["user_id"]),
                        action_type="auth.login_failed",
                        user_id=row["user_id"],
                        decision_data={"reason": "mfa_failed"},
                    )
                    raise await self._fail_login(conn, row["user_id"], now)
                totp_verified_now = True

            cancels_deletion = row["status"] == "PENDING_DELETION"
            if cancels_deletion:
                success_row = await conn.fetchrow(
                    "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
                    "last_login_at = now(), status = 'ACTIVE', deletion_requested_at = NULL, "
                    "mfa_verified_at = CASE WHEN $2 THEN now() ELSE mfa_verified_at END "
                    "WHERE user_id = $1 "
                    "AND (locked_until IS NULL OR locked_until <= clock_timestamp()) "
                    "RETURNING user_id",
                    row["user_id"],
                    totp_verified_now,
                )
            else:
                success_row = await conn.fetchrow(
                    "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
                    "last_login_at = now(), "
                    "mfa_verified_at = CASE WHEN $2 THEN now() ELSE mfa_verified_at END "
                    "WHERE user_id = $1 "
                    "AND (locked_until IS NULL OR locked_until <= clock_timestamp()) "
                    "RETURNING user_id",
                    row["user_id"],
                    totp_verified_now,
                )

            if success_row is None:
                # standard-105 conditional UPDATE: if a separate connection
                # committed 5 failures and set the lock after the initial
                # SELECT, this UPDATE's WHERE (re-checking locked_until)
                # matches 0 rows even though the password is correct — refetch
                # the latest lock state and reject (AUDIT_2026-09-30_auth_rls.md
                # F2 fix; not a wrong-credentials case, a lockout race).
                lock_row = await conn.fetchrow(
                    "SELECT locked_until, clock_timestamp() AS server_now "
                    "FROM users WHERE user_id = $1",
                    row["user_id"],
                )
                await record_audit_log(
                    conn,
                    actor_agent=str(row["user_id"]),
                    action_type="auth.login_failed",
                    user_id=row["user_id"],
                    decision_data={"reason": "account_locked_race"},
                )
                raise AccountLockedError(
                    lockout.retry_after_seconds(lock_row["locked_until"], lock_row["server_now"])
                )

            await record_audit_log(
                conn,
                actor_agent=str(row["user_id"]),
                action_type="auth.login_success",
                user_id=row["user_id"],
                decision_data={"mfa_verified_now": totp_verified_now},
            )
        final_status = "ACTIVE" if cancels_deletion else row["status"]
        final_mfa_verified_at = now if totp_verified_now else row["mfa_verified_at"]
        return _row_to_user(
            {**dict(row), "status": final_status, "mfa_verified_at": final_mfa_verified_at}
        )

    def issue_token(self, user: User) -> str:
        payload = {
            "sub": str(user.user_id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=self._jwt_expire_minutes),
        }
        return jwt.encode(payload, self._jwt_secret_key, algorithm=self._jwt_algorithm)

    async def _fail_login(
        self, conn: asyncpg.Connection, user_id: UUID, now: datetime
    ) -> AuthError:
        """Atomically increment the failure counter; the caller raises the
        returned exception (to preserve the `except ... from None` chain in the
        caller). A single UPDATE ... RETURNING in lockout lets us decide lockout
        with the latest count, without TOCTOU."""
        state = await lockout.register_failed_attempt(conn, user_id, now=now)
        if state.locked:
            # Separate event from the failed attempt itself — the fact that a lock
            # "just got applied" is a signal worthy of alerting/monitoring for
            # operators.
            await record_audit_log(
                conn,
                actor_agent=str(user_id),
                action_type="auth.account_locked",
                user_id=user_id,
                decision_data={
                    "failed_attempts": state.failed_attempts,
                    "locked_minutes": lockout.LOCKOUT_MINUTES,
                },
            )
            return AccountLockedError(state.retry_after_seconds)
        return AuthError(_GENERIC_AUTH_ERROR)
