"""L4-08 DoD — `provider_event_inbox`/`fills`/`order_idempotency` 실DB 어댑터:
ON CONFLICT 중복 흡수, 스코프 선점(NEW/EXISTING/DIGEST_MISMATCH), ttl 재선점.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-08, §5.1 inbox/
fills/idempotency 행, §5.2(스코프·digest), §6 F9(중복 전달 흡수).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.idempotency_repository import IdempotencyRepository
from src.services.oms.adapters.inbox_repository import InboxRepository
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from src.services.oms.domain.errors import IdempotencyDigestMismatchError
from src.services.oms.ports.repository import FillRepoPort, IdempotencyRepoPort, InboxRepoPort
from tests.integration.oms.conftest import create_test_user, insert_order


def test_inbox_repository_satisfies_port():
    assert isinstance(InboxRepository(), InboxRepoPort)


def test_fills_repository_satisfies_port():
    assert isinstance(FillsRepository(), FillRepoPort)


def test_idempotency_repository_satisfies_port():
    assert isinstance(IdempotencyRepository(), IdempotencyRepoPort)


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _provider_event() -> ProviderOrderEvent:
    return ProviderOrderEvent(
        provider_event_id=f"ev-{uuid4().hex}",
        venue="bitget",
        venue_symbol="BTCUSDT",
        exchange_order_id="ex-1",
        client_order_id="cid-1",
        venue_status="FILLED",
        filled_quantity=Decimal("1"),
        average_price=Decimal("100"),
        last_fill=None,
        venue_ts=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
        source="WS",
        raw_hash=_hash("raw"),
    )


async def _order_id(pool, **kwargs) -> object:
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        return await insert_order(conn, user_id, **kwargs)


# ---- inbox -----------------------------------------------------------------------


async def test_inbox_insert_if_absent_duplicate_event_absorbed_1000_times(pool):
    """DoD(3) — 같은 이벤트 1000회 삽입 → 1행. 판정은 RETURNING(불리언)으로만
    한다(카운트 SELECT 금지)."""
    repo = InboxRepository()
    ev = _provider_event()

    async with pool.acquire() as conn:
        results = [await repo.insert_if_absent(conn, ev) for _ in range(1000)]

    assert results[0] is True
    assert all(r is False for r in results[1:])
    assert sum(results) == 1


def _find_by_event_id(rows, provider_event_id: str):
    """다른 테스트가 남긴 미처리 행이 같은 공유 테스트 DB에 섞여 있을 수
    있어(§9 L4-08 dedup 테스트는 정리하지 않는다) 자기 행만 골라낸다."""
    matches = [r for r in rows if r.provider_event_id == provider_event_id]
    assert len(matches) == 1
    return matches[0]


async def test_inbox_claim_unprocessed_then_mark_processed(pool):
    repo = InboxRepository()
    ev = _provider_event()

    async with pool.acquire() as conn, conn.transaction():
        assert await repo.insert_if_absent(conn, ev) is True
        rows = await repo.claim_unprocessed(conn, limit=1000)
        row = _find_by_event_id(rows, ev.provider_event_id)
        await repo.mark_processed(conn, row.id)

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            ev.provider_event_id,
        )
    assert state == "PROCESSED"


async def test_inbox_mark_processed_rejects_wrong_expected_state(pool):
    """negative — 이미 PROCESSED인 행을 다시 NEW 기대로 처리하면 거부된다."""
    repo = InboxRepository()
    ev = _provider_event()

    async with pool.acquire() as conn:
        await repo.insert_if_absent(conn, ev)
        rows = await repo.claim_unprocessed(conn, limit=1000)
        row = _find_by_event_id(rows, ev.provider_event_id)
        await repo.mark_processed(conn, row.id)
        with pytest.raises(ConcurrencyConflictError):
            await repo.mark_processed(conn, row.id, expected_state="NEW")


# ---- fills -------------------------------------------------------------------------


async def test_fills_insert_if_absent_duplicate_fill_absorbed_1000_times(pool):
    """DoD(3) — fills도 동일. 삽입은 정확히 1회만 성공한다."""
    order_id = await _order_id(pool, quantity=Decimal("5"))
    repo = FillsRepository()
    fill = FillEvent(
        provider_fill_id=f"fill-{uuid4().hex}",
        venue="bitget",
        order_id=order_id,
        exchange_order_id="ex-1",
        symbol="BTC/USDT",
        side="BUY",
        quantity=Decimal("2"),
        price=Decimal("100"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=datetime.now(timezone.utc),
    )

    async with pool.acquire() as conn:
        results = [await repo.insert_if_absent(conn, fill) for _ in range(1000)]

    assert results[0] is True
    assert all(r is False for r in results[1:])

    async with pool.acquire() as conn:
        filled = await conn.fetchval(
            "SELECT filled_quantity FROM orders WHERE order_id = $1", order_id
        )
    assert filled == Decimal("2")  # 삽입이 1번뿐이라 합산도 1번분(중복 반영 없음)


async def test_fills_insert_recomputes_filled_quantity_from_sum_not_increment(pool):
    order_id = await _order_id(pool, quantity=Decimal("5"))
    repo = FillsRepository()

    async def _insert(qty: Decimal, fill_id: str) -> None:
        fill = FillEvent(
            provider_fill_id=fill_id, venue="bitget", order_id=order_id,
            exchange_order_id="ex-1", symbol="BTC/USDT", side="BUY", quantity=qty,
            price=Decimal("100"), fee=Decimal("0"), fee_currency="USDT",
            liquidity="TAKER", venue_ts=datetime.now(timezone.utc),
        )
        async with pool.acquire() as conn:
            assert await repo.insert_if_absent(conn, fill) is True

    fill_id_a, fill_id_b = f"fill-a-{uuid4().hex}", f"fill-b-{uuid4().hex}"
    await _insert(Decimal("1"), fill_id_a)
    await _insert(Decimal("1.5"), fill_id_b)

    async with pool.acquire() as conn:
        filled = await conn.fetchval(
            "SELECT filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills = await repo.list_for_order(conn, order_id)
    assert filled == Decimal("2.5")
    assert {f.provider_fill_id for f in fills} == {fill_id_a, fill_id_b}


async def test_fills_insert_without_order_id_skips_recalc(pool):
    """order_id가 없는(미매칭) fill은 재계산을 건드리지 않는다(NULL FK 허용)."""
    repo = FillsRepository()
    fill = FillEvent(
        provider_fill_id=f"fill-{uuid4().hex}", venue="bitget", order_id=None,
        exchange_order_id="ex-unmatched", symbol="BTC/USDT", side="SELL",
        quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_currency="USDT", liquidity="MAKER", venue_ts=datetime.now(timezone.utc),
    )
    async with pool.acquire() as conn:
        assert await repo.insert_if_absent(conn, fill) is True


# ---- idempotency ---------------------------------------------------------------


async def test_idempotency_claim_new_then_existing_for_same_digest(pool):
    repo = IdempotencyRepository()
    order_a = await _order_id(pool)
    order_b = await _order_id(pool)
    scope_hash = _hash("scope", uuid4().hex)
    digest = _hash("digest-A")

    async with pool.acquire() as conn:
        first = await repo.claim(
            conn, scope_hash=scope_hash, digest=digest, order_id=order_a,
            ttl=timedelta(minutes=5),
        )
        # 다른 order_id로 재시도해도(예: 재시도 경로) 최초 선점을 덮어쓰지 않는다.
        second = await repo.claim(
            conn, scope_hash=scope_hash, digest=digest, order_id=order_b,
            ttl=timedelta(minutes=5),
        )

    assert (first.kind, first.order_id) == ("NEW", order_a)
    assert (second.kind, second.order_id) == ("EXISTING", order_a)


async def test_idempotency_claim_digest_mismatch_raises(pool):
    """negative — 같은 scope, 다른 내용은 상위 버그 신호로 즉시 거부된다."""
    repo = IdempotencyRepository()
    order_a = await _order_id(pool)
    order_b = await _order_id(pool)
    scope_hash = _hash("scope", uuid4().hex)

    async with pool.acquire() as conn:
        await repo.claim(
            conn, scope_hash=scope_hash, digest=_hash("digest-A"), order_id=order_a,
            ttl=timedelta(minutes=5),
        )
        with pytest.raises(IdempotencyDigestMismatchError):
            await repo.claim(
                conn, scope_hash=scope_hash, digest=_hash("digest-B"), order_id=order_b,
                ttl=timedelta(minutes=5),
            )


async def test_idempotency_claim_reclaims_after_ttl_expiry(pool):
    """DoD(4) negative — ttl 만료 후에는 다른 digest라도 재선점(NEW)된다."""
    repo = IdempotencyRepository()
    order_a = await _order_id(pool)
    order_b = await _order_id(pool)
    scope_hash = _hash("scope", uuid4().hex)

    async with pool.acquire() as conn:
        first = await repo.claim(
            conn, scope_hash=scope_hash, digest=_hash("digest-A"), order_id=order_a,
            ttl=timedelta(minutes=5),
        )
        assert first.kind == "NEW"
        await conn.execute(
            "UPDATE order_idempotency SET expires_at = now() - interval '1 second' "
            "WHERE scope_hash = $1",
            scope_hash,
        )
        reclaimed = await repo.claim(
            conn, scope_hash=scope_hash, digest=_hash("digest-B"), order_id=order_b,
            ttl=timedelta(minutes=5),
        )

    assert (reclaimed.kind, reclaimed.order_id) == ("NEW", order_b)
