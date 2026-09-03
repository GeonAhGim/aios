"""PLT-33 §10-8 — exchange_credentials UNIQUE(user_id, exchange, scope) 격리.

프론트가 아직 scope를 보내지 않으므로 `ExchangeCredentialService`는 항상
scope="PAPER"만 읽고 쓴다(ADR-2026-08-29-E). 이 테스트는 (1) 마이그레이션
이 UNIQUE를 (user_id, exchange, scope)로 넓혀 같은 조합의 PAPER/LIVE 행이
공존할 수 있음을 증명하고, (2) 그렇게 공존하는 LIVE 행이 PAPER 전용
서비스 경로(get_decrypted/list_for_user/revoke)로는 절대 보이지 않음을
증명한다 — LIVE 키는 이 서비스로 열리는 순간이 없어야 한다(I7과 동일한
정신, §10-8).
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from src.core.security.encryption import legacy_encrypt
from src.core.security.key_ring import KeyRing
from src.services.exchange_credential_service import ExchangeCredentialService
from tests.integration.conftest import create_test_user

ENCRYPTION_KEY = "22" * 32


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


class _FakeAdapter:
    async def get_balance(self):
        return []

    async def aclose(self):
        return None


@pytest.fixture
def service(pool):
    key_ring = KeyRing.from_legacy_hex(ENCRYPTION_KEY)
    return ExchangeCredentialService(
        pool, key_ring=key_ring, adapter_factory=lambda *a, **k: _FakeAdapter()
    )


async def _insert_live_row(pool: asyncpg.Pool, user_id) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, scope, key_version, "
            " api_key_encrypted, api_secret_encrypted, extra_encrypted) "
            "VALUES ($1, 'bitget', 'LIVE', 'legacy', $2, $3, $4)",
            user_id,
            legacy_encrypt("live-key", ENCRYPTION_KEY).encode("ascii"),
            legacy_encrypt("live-secret", ENCRYPTION_KEY).encode("ascii"),
            legacy_encrypt("{}", ENCRYPTION_KEY).encode("ascii"),
        )


async def test_paper_and_live_rows_coexist_for_same_user_and_exchange(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")

    await _insert_live_row(pool, user_id)  # UNIQUE(user_id, exchange, scope)라 충돌 없어야 함

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT scope FROM exchange_credentials WHERE user_id = $1 ORDER BY scope", user_id
        )
    assert [r["scope"] for r in rows] == ["LIVE", "PAPER"]


async def test_get_decrypted_never_returns_live_scope_row(service, pool):
    user_id = await create_test_user(pool)
    await _insert_live_row(pool, user_id)  # PAPER 행은 등록하지 않음

    result = await service.get_decrypted(user_id, "bitget")

    assert result is None


async def test_list_for_user_excludes_live_scope_row(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")
    await _insert_live_row(pool, user_id)

    summaries = await service.list_for_user(user_id)

    assert len(summaries) == 1
    assert summaries[0].exchange == "bitget"


async def test_revoke_does_not_touch_live_scope_row(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")
    await _insert_live_row(pool, user_id)

    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        live_active = await conn.fetchval(
            "SELECT is_active FROM exchange_credentials "
            "WHERE user_id = $1 AND scope = 'LIVE'",
            user_id,
        )
    assert live_active is True
