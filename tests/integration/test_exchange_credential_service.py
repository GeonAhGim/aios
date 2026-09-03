"""12.2/12.3 통합테스트 — 실제 dev DB 대상.

실제 Bitget/KIS Demo API 키가 아직 없어(.env 비어있음), get_balance()
호출을 가로채는 가짜 adapter_factory를 주입해 검증한다(이 세션에서 반복
적용한 DI 패턴).
"""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.security.key_ring import KeyRing
from src.services.exchange_credential_service import (
    ExchangeCredentialError,
    ExchangeCredentialService,
)
from tests.integration.conftest import create_test_user

ENCRYPTION_KEY = "22" * 32


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


class _FakeAdapter:
    def __init__(self, *, fail: bool = False):
        self._fail = fail
        self.closed = False

    async def get_balance(self):
        if self._fail:
            raise RuntimeError("invalid credentials")
        return []

    async def aclose(self):
        self.closed = True


def _factory(valid_credentials, created_adapters):
    def factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        adapter = _FakeAdapter(fail=(api_key, api_secret) not in valid_credentials)
        created_adapters.append(adapter)
        return adapter

    return factory


@pytest.fixture
def created_adapters():
    return []


@pytest.fixture
def service(pool, created_adapters):
    valid = {("good-key", "good-secret")}
    key_ring = KeyRing.from_legacy_hex(ENCRYPTION_KEY)
    return ExchangeCredentialService(
        pool, key_ring=key_ring, adapter_factory=_factory(valid, created_adapters)
    )


async def test_register_succeeds_and_closes_adapter(service, pool, created_adapters):
    user_id = await create_test_user(pool)

    summary = await service.register(user_id, "bitget", "good-key", "good-secret")

    assert summary.exchange == "bitget"
    assert summary.is_active is True
    assert summary.withdrawal_permission_warning is not None
    assert created_adapters[0].closed is True


async def test_register_rejects_invalid_credentials_without_storing(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ExchangeCredentialError):
        await service.register(user_id, "bitget", "bad-key", "bad-secret")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM exchange_credentials WHERE user_id = $1", user_id
        )
    assert count == 0


async def test_credentials_stored_encrypted_not_plaintext(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "good-key", "good-secret")

    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT api_key_encrypted FROM exchange_credentials WHERE user_id = $1", user_id
        )
    assert bytes(raw) != b"good-key"


async def test_re_registering_same_exchange_replaces_credentials(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "good-key", "good-secret")
    await service.register(user_id, "bitget", "good-key", "good-secret", extra={"note": "v2"})

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM exchange_credentials WHERE user_id = $1", user_id
        )
    assert count == 1


async def test_revoke_marks_inactive_not_deleted(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "good-key", "good-secret")

    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_active, revoked_at FROM exchange_credentials WHERE user_id = $1", user_id
        )
    assert row["is_active"] is False
    assert row["revoked_at"] is not None


async def test_register_and_revoke_are_audit_logged(service, pool):
    """FD-7.2 감사기록 — api_key/api_secret 값은 어디에도 없어야 한다."""
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "good-key", "good-secret")
    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action_type, decision_data FROM audit_log "
            "WHERE user_id = $1 ORDER BY created_at",
            user_id,
        )
    action_types = [r["action_type"] for r in rows]
    assert "exchange_credential.registered" in action_types
    assert "exchange_credential.revoked" in action_types
    for row in rows:
        assert "good-key" not in row["decision_data"]
        assert "good-secret" not in row["decision_data"]


async def test_revoke_without_active_credential_raises(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ExchangeCredentialError):
        await service.revoke(user_id, "bitget")


async def test_get_decrypted_round_trips(service, pool):
    user_id = await create_test_user(pool)
    await service.register(
        user_id, "bitget", "good-key", "good-secret", extra={"api_passphrase": "pp"}
    )

    result = await service.get_decrypted(user_id, "bitget")

    assert result == ("good-key", "good-secret", {"api_passphrase": "pp"})


async def test_get_decrypted_returns_none_for_revoked_credential(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "good-key", "good-secret")
    await service.revoke(user_id, "bitget")

    result = await service.get_decrypted(user_id, "bitget")

    assert result is None


async def test_get_decrypted_reads_legacy_kid_rows(service, pool):
    """PLT-33 §9 DoD — 마이그레이션 이전에 `legacy_encrypt`(접두 없는 포맷)로
    쓰인 행이 새 `key_ring=`/`scope` 배선에서도 그대로 복호돼야 무중단 전환이다."""
    from src.core.security.encryption import legacy_encrypt

    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, scope, key_version, "
            " api_key_encrypted, api_secret_encrypted, extra_encrypted) "
            "VALUES ($1, 'bitget', 'PAPER', 'legacy', $2, $3, $4)",
            user_id,
            legacy_encrypt("good-key", ENCRYPTION_KEY).encode("ascii"),
            legacy_encrypt("good-secret", ENCRYPTION_KEY).encode("ascii"),
            legacy_encrypt("{}", ENCRYPTION_KEY).encode("ascii"),
        )

    result = await service.get_decrypted(user_id, "bitget")

    assert result == ("good-key", "good-secret", {})
