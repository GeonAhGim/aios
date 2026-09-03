"""11.2 — 회원가입/로그인 서비스 (AuthService).

Spec: 기능설계문서_v1.20.md#FD-11.1, 13_multi_tenancy_auth_v1.4.md#§13.2

FastAPI 라우터(작업트리 16번, API 조립 단계)는 아직 없다 — 이 세션의 다른
안전장치·승인 서비스(ApprovalService, ReconciliationService 등)와 동일하게
순수 서비스 계층만 지금 구현하고, 라우터는 조립 단계에서 이 클래스를
그대로 호출한다.

편차: 13번 §13.2 users DDL에 로그인 실패 잠금 상태를 저장할 컬럼이 없어
failed_login_attempts/locked_until을 신설했다(문서 v1.4, 마이그레이션
b2c3d4e5f6a7 참조).

MFA(TOTP) 검증은 FD-11.2(작업트리 11.3)에서 별도 구현 예정 — 아직 없어
verify_totp DI 콜백으로 주입받는다(이 세션에서 반복 적용한 패턴,
WatchdogService.compute_equity/SurgeDetector.verify_provenance 등과 동일).
콜백을 넘기지 않았는데 mfa_enabled=true인 계정이 로그인을 시도하면
안전하게 실패 처리한다(fail-safe — 검증 불가를 통과로 취급하지 않음).

11.6 연동 — PENDING_DELETION(FD-11.4 탈퇴 유예기간) 상태에서 로그인에
성공하면 탈퇴가 자동 취소된다(ACTIVE로 복귀, deletion_requested_at
초기화) — FD-11.4 원문 "유예기간 중 재로그인 시 탈퇴 취소 가능"의
실제 강제 지점.

PLT-22 연동 — 로그인 실패 카운터 증가는 `src/services/auth/lockout.py`의
원자 UPDATE에 위임한다(TOCTOU 제거, task-852). 잠금 판정을 받으면
`AccountLockedError`(§3.3 AUTH_ACCOUNT_LOCKED·423 계약, retry_after_seconds
보유)를 던진다 — 라우터가 아직 이관되지 않아(§9 PLT-24) 지금은 AuthError
서브클래스로 기존 401 매핑을 그대로 탄다.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.services.auth import lockout

MIN_PASSWORD_LENGTH = 12

_GENERIC_AUTH_ERROR = "이메일 또는 비밀번호가 올바르지 않습니다."

_hasher = PasswordHasher()
# 레드팀 감사(docs/RED_TEAM_FINDINGS.md #12) 반영 — 계정 미존재/정지/잠김
# 경로가 실제 Argon2 verify()를 타지 않아 마지막(존재하는 계정+틀린
# 비밀번호) 경로보다 훨씬 빨리 응답했다. 동일 메시지를 반환하면서도
# 처리시간이 다르면 그 자체가 계정 존재 여부를 드러내는 타이밍
# 사이드채널이 된다 — 모든 실패 경로에서 고정 더미 해시를 검증해
# 처리시간을 실제 사용자 경로와 비슷하게 맞춘다.
_DUMMY_PASSWORD_HASH = _hasher.hash("timing-normalization-dummy-password")

VerifyTotpFn = Callable[[UUID, str, str], Awaitable[bool]]


def _consume_verify_timing(password: str) -> None:
    try:
        _hasher.verify(_DUMMY_PASSWORD_HASH, password)
    except VerifyMismatchError:
        pass


class AuthError(Exception):
    """FD-11.1 인증/가입 실패 — 라우터가 적절한 HTTP 상태코드로 변환."""


class AccountLockedError(AuthError):
    """PLT-22 — 잠금 상태 로그인 시도. §3.3 AUTH_ACCOUNT_LOCKED(423) 계약대로
    `error_code`/`retry_after_seconds` 이름을 고정한다(프론트 deriveLockout,
    task-387이 이 이름으로 읽는다). AuthError를 상속해 라우터 이관 전까지는
    기존 계정열거 방지 매핑(모든 AuthError → 401)을 그대로 탄다."""

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
    """Draft 강도 규칙(FD-11.1): 최소 12자 + 대소문자·숫자·특수문자."""
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
    """앱 조립 단계(get_current_user JWT 검증)가 사용하는 공개 조회 함수."""
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

        async with self._pool.acquire() as conn:
            existing = await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email)
            if existing is not None:
                raise AuthError("이미 등록된 이메일입니다.")

            password_hash = _hasher.hash(password)
            row = await conn.fetchrow(
                "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING *",
                email,
                password_hash,
            )
        return _row_to_user(row)

    async def authenticate(
        self, email: str, password: str, *, totp_code: str | None = None
    ) -> User:
        """FD-11.1 예외상황 3종(계정 미존재/잠금/SUSPENDED·DELETED) 전부 여기서
        처리, 전부 동일한 일반화 메시지로 라우터에 전달 — 계정열거 공격 차단."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
            if row is None:
                _consume_verify_timing(password)
                # 비밀번호/TOTP 값은 절대 기록하지 않는다 — 시도한 이메일과
                # 결과 사유만 남긴다(계정이 없어 user_id 자체가 없음).
                await record_audit_log(
                    conn, actor_agent="unknown", action_type="auth.login_failed",
                    decision_data={"email": email, "reason": "account_not_found"},
                )
                raise AuthError(_GENERIC_AUTH_ERROR)

            if row["status"] in ("SUSPENDED", "DELETED"):
                _consume_verify_timing(password)
                await record_audit_log(
                    conn, actor_agent=str(row["user_id"]), action_type="auth.login_failed",
                    user_id=row["user_id"],
                    decision_data={
                        "reason": "account_suspended_or_deleted", "status": row["status"]
                    },
                )
                raise AuthError(_GENERIC_AUTH_ERROR)

            now = datetime.now(timezone.utc)
            if row["locked_until"] is not None and now < row["locked_until"]:
                _consume_verify_timing(password)
                await record_audit_log(
                    conn, actor_agent=str(row["user_id"]), action_type="auth.login_failed",
                    user_id=row["user_id"], decision_data={"reason": "account_locked"},
                )
                raise AccountLockedError(lockout.retry_after_seconds(row["locked_until"], now))

            try:
                _hasher.verify(row["password_hash"], password)
            except VerifyMismatchError:
                await record_audit_log(
                    conn, actor_agent=str(row["user_id"]), action_type="auth.login_failed",
                    user_id=row["user_id"], decision_data={"reason": "wrong_password"},
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
                        conn, actor_agent=str(row["user_id"]), action_type="auth.login_failed",
                        user_id=row["user_id"], decision_data={"reason": "mfa_failed"},
                    )
                    raise await self._fail_login(conn, row["user_id"], now)
                totp_verified_now = True

            await record_audit_log(
                conn, actor_agent=str(row["user_id"]), action_type="auth.login_success",
                user_id=row["user_id"], decision_data={"mfa_verified_now": totp_verified_now},
            )

            cancels_deletion = row["status"] == "PENDING_DELETION"
            if cancels_deletion:
                await conn.execute(
                    "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
                    "last_login_at = now(), status = 'ACTIVE', deletion_requested_at = NULL, "
                    "mfa_verified_at = CASE WHEN $2 THEN now() ELSE mfa_verified_at END "
                    "WHERE user_id = $1",
                    row["user_id"],
                    totp_verified_now,
                )
            else:
                await conn.execute(
                    "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
                    "last_login_at = now(), "
                    "mfa_verified_at = CASE WHEN $2 THEN now() ELSE mfa_verified_at END "
                    "WHERE user_id = $1",
                    row["user_id"],
                    totp_verified_now,
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
        """실패 카운터를 원자적으로 증가시키고, 반환된 예외를 호출자가
        `raise`한다(예외를 직접 던지지 않는 이유: 호출부의 `except ... from
        None` 체이닝을 그대로 유지하기 위해). lockout.register_failed_attempt가
        단일 UPDATE ... RETURNING이라 이 메서드 자체는 TOCTOU 없이 정확한
        최신 카운트를 기준으로 잠금 여부를 판정한다."""
        state = await lockout.register_failed_attempt(conn, user_id, now=now)
        if state.locked:
            # 실패 시도 자체와는 별개의 이벤트 — 잠금이 "지금 막 걸렸다"는
            # 사실 자체가 운영자에게 알림/모니터링 대상이 되는 신호다.
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="auth.account_locked",
                user_id=user_id,
                decision_data={
                    "failed_attempts": state.failed_attempts,
                    "locked_minutes": lockout.LOCKOUT_MINUTES,
                },
            )
            return AccountLockedError(state.retry_after_seconds)
        return AuthError(_GENERIC_AUTH_ERROR)
