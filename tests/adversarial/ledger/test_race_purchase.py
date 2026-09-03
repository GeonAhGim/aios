"""LC-17 적대적 — 동시 구매 레이스: 잔액 10,000에 9,000짜리 구매 5건 동시.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LC-17
("잔액 10,000에 9,000짜리 구매 5건 동시 → 정확히 1건 성공, Σ=0").

LC-13(`purchase_flow.place_hold`)은 같은 (purpose, reference) 홀드
충돌만 막는다(task-424, `test_place_hold_concurrent_same_reference_
only_one_succeeds`가 이미 검증) — 여기서 재현하려는 레이스는 그것과
다르다: 서로 다른 리스팅(서로 다른 reference)이 **같은 buyer 잔액**을
동시에 두고 경쟁하는 상황이다. 실제 직렬화 지점은 `post_entry`가
`balances.get_for_update`로 buyer `AVAILABLE` 계정 행에 거는
`SELECT ... FOR UPDATE`(`postgres_balance_repository.py`)이고, 먼저
커밋한 트랜잭션 이후의 나머지는 `InsufficientAvailableError`→
`InsufficientWalletBalanceError`(402, `purchase_service.py`)로 거부돼야
한다.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import UUID, uuid4

from src.foundation.ledger.contracts.v1 import Side, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.services.listing_service import ListingService
from src.services.purchase_service import (
    InsufficientWalletBalanceError,
    PurchaseResult,
    PurchaseService,
)
from src.services.verification_service import VerificationService
from tests.integration.conftest import create_test_user

_PRICE = Decimal("9000.00")
_STARTING_BALANCE = Decimal("10000.00")
_CONCURRENT_ATTEMPTS = 5


async def _seed_available(pool, user_id: UUID, amount: Decimal) -> None:
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
            code,
            amount,
        )
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = $2",
            user_id,
            amount,
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
    strategy_id = f"test-race-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent) "
            "VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')",
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id: str, version: str) -> bool:
    return True


async def _listed_listing(pool, seller: UUID, price: Decimal) -> int:
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, price)
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    return approved.listing_id


async def test_five_concurrent_purchases_against_9000_hold_exactly_one_succeeds(pool):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    await _seed_available(pool, buyer, _STARTING_BALANCE)
    listing_ids = [
        await _listed_listing(pool, seller, _PRICE) for _ in range(_CONCURRENT_ATTEMPTS)
    ]
    service = PurchaseService(pool)

    results = await asyncio.gather(
        *[service.purchase(buyer, listing_id) for listing_id in listing_ids],
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, PurchaseResult)]
    failures = [r for r in results if isinstance(r, InsufficientWalletBalanceError)]
    unexpected = [
        r for r in results if not isinstance(r, (PurchaseResult, InsufficientWalletBalanceError))
    ]

    assert unexpected == []  # 잔액 부족(402) 외 다른 실패 사유가 섞이면 즉시 드러나야 한다.
    assert len(successes) == 1
    assert len(failures) == _CONCURRENT_ATTEMPTS - 1
    assert await _available(pool, buyer) == _STARTING_BALANCE - _PRICE

    reference = f"purchase:{successes[0].purchase_id}"
    async with pool.acquire() as conn:
        lines = await conn.fetch(
            "SELECT pl.side, pl.amount FROM ledger_posting_line pl "
            "JOIN ledger_journal_entry e ON e.entry_id = pl.entry_id "
            "WHERE e.event_ref LIKE $1",
            f"{reference}%",
        )
    assert lines  # 성공한 구매 1건이 실제로 분개를 남겼다(빈 결과면 위 성공 판정이 거짓양성).
    net = sum(
        (row["amount"] if row["side"] == Side.DEBIT.value else -row["amount"]) for row in lines
    )
    assert net == Decimal("0")  # 복식부기 항등식 — 실패한 4건의 흔적이 조금이라도 섞이면 깨진다.
