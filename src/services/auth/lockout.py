"""PLT-22 — Atomicity of login-failure lockout.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-22

The previous AuthService._register_failed_attempt() read
failed_login_attempts via SELECT, incremented in Python, and wrote back
via UPDATE (a read-modify-write two-round-trip = TOCTOU race). When N
concurrent requests all read the same snapshot, some increments overwrite
each other and are lost — the same defect class as the task-329 mandate
concurrent-activate race. Here we collapse the increment and lock-out
decision into a single `UPDATE ... RETURNING`, eliminating the race:
Postgres serialises concurrent UPDATEs on the target row by acquiring a
row lock before reading or writing the value.

The router layer (§9 PLT-24, `routers/auth.py` rewrite pending) still
does not translate `AccountLockedError` into a 423 response with
`retry_after_seconds` — for now `AccountLockedError` subclasses
`AuthError`, so it rides the existing account-enumeration-prevention
mapping (`exception_mapping.py`: every AuthError → 401
AUTH_INVALID_CREDENTIALS), meaning there is no behavioural regression.
The attribute names error_code / retry_after_seconds were aligned with
the already-merged §3.3 error taxonomy and the frontend deriveLockout
(task-387) so that the router-migration leaf can reuse them unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@dataclass(frozen=True)
class LockoutState:
    failed_attempts: int
    locked: bool
    retry_after_seconds: int | None


def retry_after_seconds(locked_until: datetime | None, now: datetime) -> int | None:
    """Seconds remaining until lock expiry. Returns None if not locked.

    The frontend deriveLockout (task-387, accountLockout.ts) clamps values
    <= 0 to a default of 60 seconds, so we guarantee a minimum of 1 second
    to prevent a rounding error from yielding 0 at the instant the lock is
    set. The upper bound is likewise part of the contract:
    `locked_until` and `now` may come from two separate
    `clock_timestamp()` calls (e.g. in register_failed_attempt's
    UPDATE...RETURNING), and floating-point truncation before converting
    to whole seconds can produce a value that exceeds LOCKOUT_MINUTES*60.
    We cap it here as part of the countdown contract (esc-ci-401c16dd420e).
    """
    if locked_until is None or locked_until <= now:
        return None
    seconds = int((locked_until - now).total_seconds())
    return max(1, min(seconds, LOCKOUT_MINUTES * 60))


async def register_failed_attempt(
    conn: asyncpg.Connection, user_id: UUID, *, now: datetime | None = None
) -> LockoutState:
    """Atomically record one failed attempt.

    The increment (`failed_login_attempts + 1`) and the lockout-threshold
    comparison happen inside SQL, so Python never writes a stale
    pre-read count — even when N concurrent calls invoke this function,
    the UPDATE statement itself serialises via row lock, so the final
    count is exactly N (zero lost-update).

    The `now` parameter remains only for signature compatibility with the
    caller (auth_service.authenticate); it is not used for time
    calculations. Because `now` is captured before a slow operation like
    password-hash verification, a call with an earlier Python `now` can
    arrive at the DB after a call with a later `now`, potentially causing
    `retry_after` to exceed the upper bound (LOCKOUT_MINUTES*60) —
    reproducible with 10 concurrent calls yielding a 902-second overshoot.
    Lock-out decisions and remaining-time calculations therefore rely
    exclusively on the DB server clock (`clock_timestamp()`): concurrent
    UPDATEs are serialised by row locks, so the server-clock call order
    always matches the actual commit order, whereas Python wall-clock
    values exposed to context switches do not.
    """
    row = await conn.fetchrow(
        """
        UPDATE users
        SET failed_login_attempts = failed_login_attempts + 1,
            locked_until = CASE
                WHEN failed_login_attempts + 1 >= $2
                     AND (locked_until IS NULL OR locked_until <= clock_timestamp())
                THEN clock_timestamp() + make_interval(mins => $3)
                ELSE locked_until
            END
        WHERE user_id = $1
        RETURNING failed_login_attempts, locked_until, clock_timestamp() AS server_now
        """,
        user_id,
        MAX_FAILED_ATTEMPTS,
        LOCKOUT_MINUTES,
    )
    if row is None:
        raise ValueError(f"lockout 대상 user_id가 존재하지 않습니다: {user_id}")

    attempts: int = row["failed_login_attempts"]
    locked_until: datetime | None = row["locked_until"]
    server_now: datetime = row["server_now"]
    return LockoutState(
        failed_attempts=attempts,
        locked=locked_until is not None and locked_until > server_now,
        retry_after_seconds=retry_after_seconds(locked_until, server_now),
    )
