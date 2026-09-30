"""11.3 — MFA (TOTP) configuration (mandatory gate).

Spec: functional_design_v1.20.md#FD-11.2, 13_multi_tenancy_auth_v1.4.md#§13.2

Policy document §4.10 cross-tenant risk 3 — MFA is enforced at user level
without exception (the autonomy target is 'approver count', not 'auth
strength'). TOTP secret is never stored in plaintext; it is encrypted with
AES-256-GCM (reuse CREDENTIAL_ENCRYPTION_KEY from 07 §7.3, via the shared
utility at src/core/security/encryption.py).

Deviation (interpretation): FD-11.2 describes the order as "verify code
→ encrypt and store mfa_secret", but MfaVerifyRequest (docs 15/16) has
no field to resend the secret — meaning the server must already remember
the secret from somewhere at verify time. Here we encrypt and store the
secret immediately at setup() into users.mfa_secret with mfa_enabled=false
to represent a "pending verification" state. verify() confirms with
mfa_enabled=true on success, or nulls out mfa_secret to destroy it on
failure (we still honour the FD-11.2 exception-principle of never leaving
a half-activated state).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pyotp
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.core.security.encryption import legacy_decrypt, legacy_encrypt

ISSUER_NAME = "AIOS"


class MfaError(Exception):
    """FD-11.2 failure — verification code mismatch, etc. Router converts to 400."""


class MfaReauthenticationRequiredError(Exception):
    """Attempt to reset already-enabled MFA without password re-authentication
    (Red Team audit #11) — mapped to AUTH_MFA_REQUIRED (403). Placed in an
    independent class because the reason differs from MfaError (missing
    re-authentication, not code mismatch)."""


class MfaSetupResult(BaseModel):
    secret: str
    provisioning_uri: str


class MfaService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        encryption_key: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._pool = pool
        self._encryption_key = encryption_key
        # Allow #13 reuse-prevention tests to deterministically reproduce
        # "next interval" without actually waiting 30 seconds
        # (same principle as clock injection in watchdog.py; runtime uses default).
        self._now = now

    async def setup(self, user_id: UUID, email: str) -> MfaSetupResult:
        secret = pyotp.random_base32()
        provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=email, issuer_name=ISSUER_NAME
        )
        encrypted_secret = legacy_encrypt(secret, self._encryption_key)

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET mfa_secret = $2, mfa_enabled = false WHERE user_id = $1",
                user_id,
                encrypted_secret,
            )
            # Never emit secret/provisioning_uri here — record only that a
            # setup attempt occurred.
            await record_audit_log(
                conn,
                actor_agent=str(user_id),
                action_type="mfa.setup",
                user_id=user_id,
                decision_data={},
            )

        return MfaSetupResult(secret=secret, provisioning_uri=provisioning_uri)

    async def verify(self, user_id: UUID, totp_code: str) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
            )
            encrypted_secret: str | None = row["mfa_secret"] if row is not None else None
            already_enabled = bool(row["mfa_enabled"]) if row is not None else False

            valid = False
            replayed = False
            if encrypted_secret is not None and self._totp_code_valid(encrypted_secret, totp_code):
                # Red Team audit (#13) — reject if the timecode was already
                # used once (replay attack prevention).
                timecode = self._current_timecode(encrypted_secret)
                valid = await self._consume_timecode(conn, user_id, timecode)
                replayed = not valid

            if not valid:
                if not already_enabled:
                    # Red Team audit (#11) — discard secret only on first-setup
                    # failure (mfa_enabled=false, pending verification).
                    # ("never leave half-activated" FD-11.2 principle applies here.)
                    await conn.execute(
                        "UPDATE users SET mfa_secret = NULL, mfa_enabled = false "
                        "WHERE user_id = $1",
                        user_id,
                    )
                    reset_reason = "replayed" if replayed else "invalid_code"
                    await record_audit_log(
                        conn,
                        actor_agent=str(user_id),
                        action_type="mfa.reset",
                        user_id=user_id,
                        decision_data={"reason": reset_reason, "stage": "initial_setup"},
                    )
                else:
                    # Never touch the row when already_enabled=true — closes
                    # an auth-bypass hole where a stolen Bearer token (without
                    # password) could send any wrong code and permanently
                    # disable already-enabled MFA remotely.
                    pass
                await record_audit_log(
                    conn,
                    actor_agent=str(user_id),
                    action_type="mfa.verify_failed",
                    user_id=user_id,
                    decision_data={"reason": "replayed" if replayed else "invalid_code"},
                )
                # This endpoint (/auth/mfa/verify) is callable only by users
                # already authenticated via Bearer token (unrelated to #12
                # login-timing side-channel), so distinguishing "replay" from
                # "wrong code" leaks no account-existence info. Blending them
                # would instead make users think 2FA is broken when they
                # retry with the same code after a failed delivery.
                if replayed:
                    raise MfaError(
                        "이미 사용한 코드입니다. 인증 앱에 새로 표시되는 코드로 다시 시도해주세요."
                    )
                raise MfaError("인증 코드가 올바르지 않습니다.")

            await conn.execute("UPDATE users SET mfa_enabled = true WHERE user_id = $1", user_id)
            await record_audit_log(
                conn,
                actor_agent=str(user_id),
                action_type="mfa.verify_success",
                user_id=user_id,
                decision_data={"already_enabled": already_enabled},
            )

    def _totp_code_valid(self, encrypted_secret: str, totp_code: str) -> bool:
        secret = legacy_decrypt(encrypted_secret, self._encryption_key)
        return bool(pyotp.totp.TOTP(secret).verify(totp_code, for_time=self._now()))

    def _current_timecode(self, encrypted_secret: str) -> int:
        secret = legacy_decrypt(encrypted_secret, self._encryption_key)
        return int(pyotp.totp.TOTP(secret).timecode(self._now()))

    async def _consume_timecode(
        self, conn: asyncpg.Connection, user_id: UUID, timecode: int
    ) -> bool:
        """Red Team audit (docs/RED_TEAM_FINDINGS.md #13) — returns False if
        this timecode has already been used (replay). Atomic conditional
        UPDATE ensures only one concurrent request with the same code passes."""
        row = await conn.fetchrow(
            "UPDATE users SET mfa_last_used_timecode = $2 "
            "WHERE user_id = $1 "
            "AND (mfa_last_used_timecode IS NULL OR mfa_last_used_timecode < $2) "
            "RETURNING user_id",
            user_id,
            timecode,
        )
        return row is not None

    async def verify_totp_for_login(
        self, user_id: UUID, encrypted_secret: str, totp_code: str
    ) -> bool:
        """Entry point injected as the verify_totp DI callback in
        AuthService.authenticate()."""
        if not self._totp_code_valid(encrypted_secret, totp_code):
            return False
        timecode = self._current_timecode(encrypted_secret)
        async with self._pool.acquire() as conn:
            return await self._consume_timecode(conn, user_id, timecode)
