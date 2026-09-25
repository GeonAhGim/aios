"""11.6 — Account Deletion API (AccountDeletionService).

Spec: FunctionalSpec_v1.20.md#FD-11.4, FD-16, Policy 8.10

The WorkTree 11 group preamble marks this as the only leaf explicitly
deferred until FD-16 (Strategy Execution Control Panel) is complete —
verifying RUNNING executions required the target table
(strategy_executions) to exist first. Now that FD-16 is done, we begin.

Re-authentication (password) is not a login attempt but a sensitive-action
confirmation within an already-authenticated session — it bypasses the
lock counter / MFA flow of AuthService.authenticate() and verifies the
password hash directly (re-auth failure must not touch the login-failure
lock counter — a separate concern).

Deletion is automatically cancelled if the user logs in during the
grace period (Draft 30 days) — AuthService.authenticate() is updated
in this leaf and, upon detecting PENDING_DELETION status, reverts it
to ACTIVE.

Actual purge procedures (PII anonymization after grace period expires,
etc.) fall under scope 19.4 legal review and are out of scope here —
we only handle the transition to PENDING_DELETION.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel

DELETION_GRACE_PERIOD_DAYS = 30  # Draft

_hasher = PasswordHasher()


class AccountDeletionError(Exception):
    """FD-11.4 failure — router converts to 400/403/404."""


class DeletionResult(BaseModel):
    status: str
    deletion_effective_at: datetime


class AccountDeletionService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def request_deletion(self, user_id: UUID, password: str) -> DeletionResult:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT password_hash FROM users WHERE user_id = $1", user_id
            )
            if row is None:
                raise AccountDeletionError("존재하지 않는 사용자입니다.")

            try:
                _hasher.verify(row["password_hash"], password)
            except VerifyMismatchError as exc:
                raise AccountDeletionError("비밀번호가 일치하지 않습니다.") from exc

            running = await conn.fetch(
                "SELECT id, strategy_id FROM strategy_executions "
                "WHERE user_id = $1 AND status = 'RUNNING'",
                user_id,
            )
            if running:
                blocking = ", ".join(f"{r['strategy_id']}(실행#{r['id']})" for r in running)
                raise AccountDeletionError(
                    f"실행중인 전략을 먼저 중지(FD-16.3)해주세요: {blocking}"
                )

            now = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE users SET status = 'PENDING_DELETION', deletion_requested_at = $2 "
                "WHERE user_id = $1",
                user_id,
                now,
            )

        return DeletionResult(
            status="PENDING_DELETION",
            deletion_effective_at=now + timedelta(days=DELETION_GRACE_PERIOD_DAYS),
        )
