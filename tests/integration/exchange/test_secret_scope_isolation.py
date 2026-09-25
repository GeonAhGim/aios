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

import asyncio
import os
import time
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from src.core.security.encryption import legacy_encrypt
from src.core.security.key_ring import KeyRing
from src.services.exchange_credential_service import ExchangeCredentialService
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

# 예산표(ADR-2026-09-09-C Decision 1)에 자격증명 복호 조회 전용 항목은
# 없다 — get_decrypted는 단일 실DB 왕복(fetchrow 1회) + 로컬 복호라는
# 점에서 "주문 제출→ACK p95 50ms(paper)"를 가장 가까운 유사 항목으로
# 차용한다(task-3160/3162 DEEPEN과 동일 차용 근거).
_GET_DECRYPTED_P95_BUDGET_MS = 50.0

ENCRYPTION_KEY = "22" * 32


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _build_fake_adapter(
    exchange: str,
    api_key: str,
    api_secret: str,
    extra: dict[str, str] | None,
    *,
    demo_mode: bool = True,
) -> FakeExchangeAdapter:
    return FakeExchangeAdapter(exchange_name=exchange)


@pytest.fixture
def service(pool: asyncpg.Pool) -> ExchangeCredentialService:
    key_ring = KeyRing.from_legacy_hex(ENCRYPTION_KEY)
    return ExchangeCredentialService(pool, key_ring=key_ring, adapter_factory=_build_fake_adapter)


async def _insert_live_row(pool: asyncpg.Pool, user_id: UUID) -> None:
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


async def test_paper_and_live_rows_coexist_for_same_user_and_exchange(
    service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")

    await _insert_live_row(pool, user_id)  # UNIQUE(user_id, exchange, scope)라 충돌 없어야 함

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT scope FROM exchange_credentials WHERE user_id = $1 ORDER BY scope", user_id
        )
    assert [r["scope"] for r in rows] == ["LIVE", "PAPER"]


async def test_get_decrypted_never_returns_live_scope_row(
    service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    user_id = await create_test_user(pool)
    await _insert_live_row(pool, user_id)  # PAPER 행은 등록하지 않음

    result = await service.get_decrypted(user_id, "bitget")

    assert result is None


async def test_list_for_user_excludes_live_scope_row(
    service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")
    await _insert_live_row(pool, user_id)

    summaries = await service.list_for_user(user_id)

    assert len(summaries) == 1
    assert summaries[0].exchange == "bitget"


async def test_revoke_does_not_touch_live_scope_row(
    service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")
    await _insert_live_row(pool, user_id)

    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        live_active = await conn.fetchval(
            "SELECT is_active FROM exchange_credentials WHERE user_id = $1 AND scope = 'LIVE'",
            user_id,
        )
    assert live_active is True


async def _get_decrypted_p95_ms(
    service: ExchangeCredentialService, user_id: UUID, exchange: str, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await service.get_decrypted(user_id, exchange)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_get_decrypted_p95_under_borrowed_order_ack_budget(
    service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    """수치 성능 단언: get_decrypted는 단일 실DB 왕복(fetchrow 1회) + 로컬
    복호라는 점에서 "주문 제출→ACK p95 50ms(paper)"를 자체 예산으로
    차용한다(task-3160/3162 DEEPEN과 동일 차용 근거). 30회 반복 p95를
    그 예산 내로 단언한다."""
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")

    p95_ms = await _get_decrypted_p95_ms(service, user_id, "bitget", n=30)

    assert p95_ms < _GET_DECRYPTED_P95_BUDGET_MS


async def test_get_decrypted_budget_gate_fails_on_injected_regression(
    monkeypatch: pytest.MonkeyPatch, service: ExchangeCredentialService, pool: asyncpg.Pool
) -> None:
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    asyncpg.Connection.fetchrow에 60ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology 아님을 증명)."""
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", "paper-key", "paper-secret")

    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(
        self: asyncpg.Connection, *args: Any, **kwargs: Any
    ) -> asyncpg.Record | None:
        await asyncio.sleep(0.06)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _get_decrypted_p95_ms(service, user_id, "bitget", n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _GET_DECRYPTED_P95_BUDGET_MS
