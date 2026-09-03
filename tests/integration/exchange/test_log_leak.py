"""PLT-33 — 예외 메시지·감사로그에 자격증명 원문이 찍히지 않는지 검증.

task-904 redaction 원칙(`KeyRing._redact_entry`와 동일한 정신)을 credential
서비스 경로 전체에 적용한다: 등록 실패, 복호 실패(회전으로 kid 소실),
감사로그 어디에도 api_key/api_secret 원문이나 원본 암호문(base64) 전체가
그대로 노출돼서는 안 된다.
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from src.core.security.key_ring import KeyRing
from src.services.exchange_credential_service import (
    ExchangeCredentialDecryptionError,
    ExchangeCredentialError,
    ExchangeCredentialService,
)
from tests.integration.conftest import create_test_user

_SECRET_KEY = "good-secret-do-not-leak"
_SECRET_API_KEY = "good-key-do-not-leak"


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


class _FakeAdapter:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def get_balance(self):
        if self._fail:
            raise RuntimeError(f"auth failed for key={_SECRET_API_KEY} secret={_SECRET_KEY}")
        return []

    async def aclose(self):
        return None


def _factory(*, fail: bool):
    def factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        return _FakeAdapter(fail=fail)

    return factory


@pytest.fixture
def key_ring_a():
    return KeyRing({"kid-a": b"\x11" * 32}, active_kid="kid-a")


@pytest.fixture
def service(pool, key_ring_a):
    return ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=False)
    )


async def test_register_failure_exception_omits_credentials(pool, key_ring_a):
    user_id = await create_test_user(pool)
    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=True)
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await failing_service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


async def test_audit_log_omits_credentials(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)
    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE user_id = $1", user_id
        )
    for row in rows:
        payload = str(row["decision_data"])
        assert _SECRET_API_KEY not in payload
        assert _SECRET_KEY not in payload


async def test_decryption_failure_after_rotation_does_not_leak_ciphertext(service, pool):
    """kid-a로 등록한 뒤 kid-a가 완전히 사라진 KeyRing으로 조회하면
    (회전 도중 구버전 kid를 너무 일찍 지운 상황을 흉내) 복호는 실패해야
    하고, 그 예외 메시지는 고정 문구여야 한다 — 원본 base64 암호문이나
    `cryptography` 라이브러리 예외의 내부 문자열이 새어나가면 안 된다."""
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    rotated_ring = KeyRing({"kid-b": b"\x22" * 32}, active_kid="kid-b")
    rotated_service = ExchangeCredentialService(pool, key_ring=rotated_ring)

    with pytest.raises(ExchangeCredentialDecryptionError) as exc_info:
        await rotated_service.get_decrypted(user_id, "bitget")

    message = str(exc_info.value)
    assert message == "저장된 자격증명을 복호할 수 없습니다."
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message
    assert "kid-a" not in message  # 존재했던 kid 값조차 echo하지 않는다


async def test_revoke_not_found_exception_omits_exchange_secrets(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await service.revoke(user_id, "bitget")

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message
