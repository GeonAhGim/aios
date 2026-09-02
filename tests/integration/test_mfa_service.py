"""11.3 통합테스트 — 실제 dev DB 대상."""
from datetime import datetime, timedelta, timezone
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


class _MutableClock:
    """#13 재사용 방지 테스트 전용 — 실제로 30초를 기다리지 않고도 서로
    다른 TOTP 타임코드 구간을 결정적으로 재현한다(watchdog.py의 주입식
    clock과 동일 원칙)."""

    def __init__(self) -> None:
        self.current = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


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

    with pytest.raises(MfaError, match="인증 코드가 올바르지 않습니다"):
        await mfa.verify(user_id, "000000")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
        )
    assert row["mfa_secret"] is None
    assert row["mfa_enabled"] is False


async def test_verify_with_wrong_code_after_already_enabled_does_not_disable_mfa(pool):
    """레드팀 감사 #11 — 이미 켜진 MFA는 탈취한 Bearer 토큰 + 틀린 코드
    한 번만으로 영구 비활성화되면 안 된다(비밀번호 없이도 가능한 인증
    우회였음)."""
    clock = _MutableClock()
    mfa = MfaService(pool, encryption_key=ENCRYPTION_KEY, now=clock)
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)
    correct_code = pyotp.totp.TOTP(result.secret).at(clock.current)
    await mfa.verify(user_id, correct_code)

    with pytest.raises(MfaError):
        await mfa.verify(user_id, "000000")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
        )
    assert row["mfa_secret"] is not None
    assert row["mfa_enabled"] is True

    # 기존 secret이 그대로 살아있으니 다음 구간의 정상 코드로는 여전히
    # 재검증 가능해야 한다(같은 구간 코드를 재사용하는 것은 #13이 막는
    # 별개의 정상 동작).
    clock.advance(31)
    still_valid_code = pyotp.totp.TOTP(result.secret).at(clock.current)
    await mfa.verify(user_id, still_valid_code)


async def test_verify_rejects_replaying_the_same_totp_code(pool):
    """docs/RED_TEAM_FINDINGS.md #13 회귀 — 같은 30초 구간 안에서 이미
    성공한 코드를 다시 보내면(유출된 코드 재사용 시나리오) valid_window
    안이라도 거부해야 한다."""
    clock = _MutableClock()
    mfa = MfaService(pool, encryption_key=ENCRYPTION_KEY, now=clock)
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)
    code = pyotp.totp.TOTP(result.secret).at(clock.current)
    await mfa.verify(user_id, code)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET mfa_enabled = false WHERE user_id = $1", user_id
        )

    # 코드 자체는 올바른 재사용이므로, "코드가 틀렸다"는 오인을 막기 위해
    # 일반 실패와는 다른 메시지여야 한다 — 사용자가 방금 전송 실패로
    # 같은 코드를 재시도한 것과 구별이 안 되면 2단계 인증이 고장난
    # 것처럼 보인다.
    with pytest.raises(MfaError, match="이미 사용한 코드"):
        await mfa.verify(user_id, code)  # 같은 구간, 같은 코드 재사용 시도


async def test_setup_verify_and_reset_are_audit_logged(mfa, pool):
    """FD-7.2 감사기록 — setup/verify_success/verify_failed(=reset, 최초
    설정 실패 시)를 남긴다. secret/TOTP 코드 값은 어디에도 없어야 한다."""
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)

    with pytest.raises(MfaError):
        await mfa.verify(user_id, "000000")

    result2 = await mfa.setup(user_id, email)
    code = pyotp.totp.TOTP(result2.secret).now()
    await mfa.verify(user_id, code)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action_type, decision_data FROM audit_log "
            "WHERE user_id = $1 ORDER BY created_at",
            user_id,
        )
    action_types = [r["action_type"] for r in rows]
    assert action_types.count("mfa.setup") == 2
    assert "mfa.reset" in action_types
    assert "mfa.verify_failed" in action_types
    assert "mfa.verify_success" in action_types
    for row in rows:
        assert result.secret not in row["decision_data"]
        assert result2.secret not in row["decision_data"]


async def test_login_with_mfa_enabled_uses_verify_totp_for_login_callback(pool):
    clock = _MutableClock()
    mfa = MfaService(pool, encryption_key=ENCRYPTION_KEY, now=clock)
    user_id, email = await _real_user(pool)
    result = await mfa.setup(user_id, email)
    code = pyotp.totp.TOTP(result.secret).at(clock.current)
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

    clock.advance(31)  # #13 반영 — 로그인 시점 코드는 설정검증 때와 다른 구간이어야 함
    login_code = pyotp.totp.TOTP(result.secret).at(clock.current)
    user = await auth.authenticate(email, _TEST_PASSWORD, totp_code=login_code)
    assert user.user_id == user_id


_TEST_PASSWORD = "Str0ng!Passw0rd"


def _hash_for_test() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(_TEST_PASSWORD)
