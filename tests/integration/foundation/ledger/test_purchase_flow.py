"""LC-13 `purchase_flow`/`purchase_service` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.5, §9 LC-13.
DoD(task-424): 동시 5건 구매 중 1건만 성공(나머지는 HOLD 충돌 거부) + 무료
리스팅은 분개 0건 + 기존 `tests/integration/test_marketplace_router.py`
무수정 통과(이 파일은 그 회귀를 건드리지 않는다 — 별도로 확인됨).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.purchase_flow import (
    HoldConflictError,
    capture_hold,
    place_hold,
    release_hold,
)
from src.foundation.ledger.contracts.v1 import HoldState, HoldView, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.hold_state import HoldExpiredError
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.verification_service import VerificationService
from tests.integration.conftest import create_test_user

_TEST_PURPOSE = "TEST_MARKETPLACE_PURCHASE"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.holds = PostgresHoldRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _seed_available(pool, user_id: UUID, amount: Decimal) -> None:
    """`user_wallets`와 `ledger_balance`를 처음부터 일치시켜 픽스처를 만든다
    — `place_hold`의 drift 재동기화(레거시 픽스처의 직접 SQL 대비)가 동시
    호출 사이에서 경합하지 않도록, 이 테스트 파일은 항상 드리프트 0에서
    시작한다(동시성 테스트가 검증하려는 것은 `ledger_hold` UNIQUE 충돌이지
    이 재동기화 자체가 아니다)."""
    code = ua(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, 'LIABILITY', 'KRW', FALSE) ON CONFLICT (account_code) DO NOTHING",
            code,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "SELECT account_id, $2, FALSE, 0 FROM ledger_account WHERE account_code = $1 "
            "ON CONFLICT (account_id) DO UPDATE SET balance = $2",
            code, amount,
        )
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = $2",
            user_id, amount,
        )


async def _available(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(user_id, UserSub.AVAILABLE),
        )
    return value if value is not None else Decimal("0")


async def _create_strategy(pool, owner_user_id: UUID) -> tuple[str, str]:
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent) "
            "VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')",
            strategy_id, version, owner_user_id, json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id: str, version: str) -> bool:
    return True


async def _listed_listing(pool, seller: UUID, price: Decimal | None):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, price)
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    return await verification_service.decide(submitted.id, verifier, "APPROVE")


async def test_place_and_capture_hold_creates_two_entries_and_settles(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("100.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-purchase:{uuid4()}"

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
            expires_at=_clock() + timedelta(minutes=15), actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
        assert hold.state == HoldState.PENDING
        mid_balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
        assert mid_balance == Decimal("0.00")

        capture = await capture_hold(
            conn, hold, seller_id=seller, commission_rate=Decimal("0.15"),
            actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )

    assert capture.hold.state == HoldState.CAPTURED
    assert capture.commission_amount == Decimal("15.00")
    assert capture.payout_amount == Decimal("85.00")

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"hold:{hold.hold_id}%",
        )
    assert entry_count == 2  # HOLD_PLACED + HOLD_CAPTURED


async def test_capture_expired_hold_is_rejected(pool, ports):
    buyer = await create_test_user(pool)
    price = Decimal("10.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-expired:{uuid4()}"
    past = _clock() - timedelta(minutes=1)

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
            expires_at=past, actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )

        with pytest.raises(HoldExpiredError):
            await capture_hold(
                conn, hold, seller_id=await create_test_user(pool), commission_rate=Decimal("0.15"),
                actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, holds=ports.holds,
            )
        # PENDING인 홀드는 release로 되돌릴 수 있다(구매가 아니어도 재사용, LC-14 대비).
        released = await release_hold(
            conn, hold, reason="test", actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
    assert released.state == HoldState.RELEASED
    assert await _available(pool, buyer) == price


async def test_place_hold_concurrent_same_reference_only_one_succeeds(pool, ports):
    buyer = await create_test_user(pool)
    price = Decimal("10.00")
    await _seed_available(pool, buyer, price * 5)
    reference = f"test-concurrent:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async def _attempt() -> HoldView:
        async with pool.acquire() as conn, conn.transaction():
            return await place_hold(
                conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
                expires_at=expires_at, actor_subject_id=buyer, trace_id=uuid4(),
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, holds=ports.holds,
            )

    results = await asyncio.gather(*[_attempt() for _ in range(5)], return_exceptions=True)
    successes = [r for r in results if isinstance(r, HoldView)]
    conflicts = [r for r in results if isinstance(r, HoldConflictError)]

    assert len(successes) == 1
    assert len(conflicts) == 4
    async with pool.acquire() as conn:
        hold_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_hold WHERE purpose = $1 AND reference = $2",
            _TEST_PURPOSE, reference,
        )
    assert hold_count == 1
    assert await _available(pool, buyer) == price * 4  # 5건 펀딩 중 1건만 실제로 차감·확정


async def test_free_listing_purchase_posts_zero_journal_entries(pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=None)
    buyer = await create_test_user(pool)
    service = PurchaseService(pool)

    result = await service.purchase(buyer, listing.listing_id)

    assert result.status == "CONFIRMED"
    assert result.platform_commission_amount is None
    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"purchase:{result.purchase_id}%",
        )
    assert entry_count == 0


async def test_purchase_service_settles_seller_and_house_via_hold_flow(pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("100.00"))
    buyer = await create_test_user(pool)
    await _seed_available(pool, buyer, Decimal("100.00"))
    service = PurchaseService(pool)

    result = await service.purchase(buyer, listing.listing_id)

    assert result.platform_commission_amount == Decimal("15.0000")
    assert result.seller_payout_amount == Decimal("85.0000")
    assert await _available(pool, buyer) == Decimal("0.00")
    assert await _available(pool, seller) == Decimal("85.00")

    reference = f"purchase:{result.purchase_id}"
    async with pool.acquire() as conn:
        hold_row = await conn.fetchrow(
            "SELECT state, entry_id, settled_entry_id FROM ledger_hold WHERE reference = $1",
            reference,
        )
        settlement_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1", f"{reference}:%"
        )
    assert hold_row["state"] == "CAPTURED"
    assert hold_row["entry_id"] is not None
    assert hold_row["settled_entry_id"] is not None
    assert settlement_count == 2  # PAYOUT_RELEASE + commission MANUAL_ADJUSTMENT
