"""11.2 통합테스트 — 실제 dev DB 대상.

5회 연속 실패 잠금(15분)의 실제 대기 대신, DB의 failed_login_attempts를
직접 4로 세팅해 "다음 실패가 5번째"인 상태를 결정적으로 재현한다.
"""
from pathlib import Path

import asyncpg
import jwt
import pytest
from dotenv import dotenv_values

from src.services.auth_service import AuthError, AuthService

JWT_SECRET = "test-secret-key-not-for-production"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def auth(pool):
    return AuthService(pool, jwt_secret_key=JWT_SECRET)


def _unique_email() -> str:
    import uuid

    return f"test-{uuid.uuid4().hex}@example.com"


STRONG_PASSWORD = "Str0ng!Passw0rd"


async def test_signup_then_login_round_trip(auth):
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)

    user = await auth.authenticate(email, STRONG_PASSWORD)

    assert user.email == email
    token = auth.issue_token(user)
    decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert decoded["sub"] == str(user.user_id)


async def test_signup_rejects_weak_password(auth):
    with pytest.raises(AuthError):
        await auth.signup(_unique_email(), "short1!")


async def test_signup_rejects_duplicate_email(auth):
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)

    with pytest.raises(AuthError):
        await auth.signup(email, STRONG_PASSWORD)


async def test_login_fails_for_nonexistent_email_with_generic_message(auth):
    with pytest.raises(AuthError, match="이메일 또는 비밀번호가 올바르지 않습니다"):
        await auth.authenticate(_unique_email(), STRONG_PASSWORD)


async def test_login_fails_for_wrong_password_with_generic_message(auth):
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)

    with pytest.raises(AuthError, match="이메일 또는 비밀번호가 올바르지 않습니다"):
        await auth.authenticate(email, "WrongPassword1!")


async def test_fifth_consecutive_failure_locks_account(auth, pool):
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET failed_login_attempts = 4 WHERE email = $1", email
        )

    with pytest.raises(AuthError):
        await auth.authenticate(email, "WrongPassword1!")

    # 잠긴 상태에서는 올바른 비밀번호로도 로그인 거부
    with pytest.raises(AuthError):
        await auth.authenticate(email, STRONG_PASSWORD)


async def test_suspended_account_login_rejected(auth, pool):
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET status = 'SUSPENDED' WHERE email = $1", email)

    with pytest.raises(AuthError):
        await auth.authenticate(email, STRONG_PASSWORD)


async def test_mfa_enabled_login_fails_without_verify_totp_callback(pool):
    auth_no_totp = AuthService(pool, jwt_secret_key=JWT_SECRET)
    email = _unique_email()
    await auth_no_totp.signup(email, STRONG_PASSWORD)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET mfa_enabled = true, mfa_secret = 'dummy' WHERE email = $1", email
        )

    with pytest.raises(AuthError):
        await auth_no_totp.authenticate(email, STRONG_PASSWORD, totp_code="123456")


async def test_mfa_enabled_login_succeeds_with_verifying_callback(pool):
    async def verify_totp(secret, code):
        return secret == "dummy" and code == "123456"

    auth_with_totp = AuthService(pool, jwt_secret_key=JWT_SECRET, verify_totp=verify_totp)
    email = _unique_email()
    await auth_with_totp.signup(email, STRONG_PASSWORD)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET mfa_enabled = true, mfa_secret = 'dummy' WHERE email = $1", email
        )

    user = await auth_with_totp.authenticate(email, STRONG_PASSWORD, totp_code="123456")
    assert user.email == email
