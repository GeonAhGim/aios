"""12.4/12.5 통합테스트 — 실제 dev DB 대상.

12.5의 완료조건("서로 다른 두 사용자가 동시에 각자의 키로 조회했을 때
섞이지 않음")을 여기서 함께 검증한다 — 별도 리프 파일을 만들 만큼 다른
관심사가 아니라 CredentialResolver 자체의 핵심 요구사항이라 같은
파일에서 다룬다.
"""
import asyncio
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.security.key_ring import KeyRing
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService
from tests.integration.conftest import create_test_user

ENCRYPTION_KEY = "33" * 32


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
    def __init__(self, api_key, api_secret, extra):
        self.api_key = api_key
        self.api_secret = api_secret
        self.extra = extra

    async def get_balance(self):
        return []


def _factory():
    calls = []

    def factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        adapter = _FakeAdapter(api_key, api_secret, extra)
        calls.append(adapter)
        return adapter

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


@pytest.fixture
def factory():
    return _factory()


@pytest.fixture
def credential_service(pool, factory):
    key_ring = KeyRing.from_legacy_hex(ENCRYPTION_KEY)
    return ExchangeCredentialService(pool, key_ring=key_ring, adapter_factory=factory)


@pytest.fixture
def resolver(credential_service, factory):
    return CredentialResolver(credential_service, adapter_factory=factory)


async def test_resolves_registered_credential_to_adapter(resolver, credential_service, pool):
    user_id = await create_test_user(pool)
    await credential_service.register(user_id, "bitget", "key-a", "secret-a")

    adapter = await resolver.get_adapter(user_id, "bitget")

    assert adapter.api_key == "key-a"


async def test_raises_for_unregistered_credential(resolver, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(CredentialNotFoundError):
        await resolver.get_adapter(user_id, "bitget")


async def test_repeated_calls_within_ttl_reuse_cached_adapter(
    resolver, credential_service, pool, factory
):
    user_id = await create_test_user(pool)
    await credential_service.register(user_id, "bitget", "key-a", "secret-a")

    first = await resolver.get_adapter(user_id, "bitget")
    second = await resolver.get_adapter(user_id, "bitget")

    assert first is second
    # register()도 자체 검증 호출로 factory를 1번 쓰므로 +1
    assert len(factory.calls) == 2


async def test_invalidate_forces_fresh_adapter_on_next_call(
    resolver, credential_service, pool, factory
):
    user_id = await create_test_user(pool)
    await credential_service.register(user_id, "bitget", "key-a", "secret-a")
    first = await resolver.get_adapter(user_id, "bitget")

    resolver.invalidate(user_id, "bitget")
    second = await resolver.get_adapter(user_id, "bitget")

    assert first is not second


async def test_two_users_concurrent_lookup_never_mix_credentials(
    resolver, credential_service, pool
):
    """12.5 완료조건 실증 — 서로 다른 두 사용자가 동시에 각자의 Bitget 키로
    조회했을 때 키가 섞이지 않아야 한다(4.10 멀티테넌시 격리 원칙)."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    await credential_service.register(user_a, "bitget", "key-a", "secret-a")
    await credential_service.register(user_b, "bitget", "key-b", "secret-b")

    adapter_a, adapter_b = await asyncio.gather(
        resolver.get_adapter(user_a, "bitget"),
        resolver.get_adapter(user_b, "bitget"),
    )

    assert adapter_a.api_key == "key-a"
    assert adapter_b.api_key == "key-b"
