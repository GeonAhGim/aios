"""11.3 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path

import asyncpg
import pyotp
import pytest
from dotenv import dotenv_values

from src.services.auth_service import AuthService
from src.services.mfa_service import MfaError, MfaService
from tests.integration.conftest import create_test_user

ENCRYPTION_KEY = "00" * 32  # 32바이트 더미 키(hex 64자) — 테스트 전용
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
def mfa(pool):
    return MfaService(pool, encryption_key=ENCRYPTION_KEY)


async def _real_user(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        email = await conn.fetchval("SELECT email FROM users WHERE user_id = $1", user_id)
    return user_id, email


async def test_setup_returns_secret_and_provisioning_uri(mfa, pool):
    user_id, email = await _real_user(pool)

    result = await mfa.setup(user_id, email)

    assert len(result.secret) > 0
    assert result.provisioning_uri.startswith("otpauth://totp/")
    assert email.split("@")[0] in result.provisioning_uri

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
        )
    assert row["mfa_secret"] is not None
    assert row["mfa_enabled"] is False


async def test_verify_with_correct_code_enables_mfa(mfa, pool):
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)
    code = pyotp.totp.TOTP(result.secret).now()

    await mfa.verify(user_id, code)

    async with pool.acquire() as conn:
        enabled = await conn.fetchval(
            "SELECT mfa_enabled FROM users WHERE user_id = $1", user_id
        )
    assert enabled is True


async def test_verify_with_wrong_code_discards_secret(mfa, pool):
    user_id, email = await _real_user(pool)
    await mfa.setup(user_id, email)

    with pytest.raises(MfaError):
        await mfa.verify(user_id, "000000")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
        )
    assert row["mfa_secret"] is None
    assert row["mfa_enabled"] is False


async def test_login_with_mfa_enabled_uses_verify_totp_for_login_callback(mfa, pool):
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)
    code = pyotp.totp.TOTP(result.secret).now()
    await mfa.verify(user_id, code)

    auth = AuthService(
        pool, jwt_secret_key=JWT_SECRET, verify_totp=mfa.verify_totp_for_login
    )

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET password_hash = $2 WHERE user_id = $1",
            user_id,
            _hash_for_test(),
        )

    login_code = pyotp.totp.TOTP(result.secret).now()
    user = await auth.authenticate(email, _TEST_PASSWORD, totp_code=login_code)
    assert user.user_id == user_id


_TEST_PASSWORD = "Str0ng!Passw0rd"


def _hash_for_test() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(_TEST_PASSWORD)
