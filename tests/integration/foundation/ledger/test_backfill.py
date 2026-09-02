"""LC-11 `backfill_ledger` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-11.
DoD: "픽스처 wallet_transactions(환불 포함) 백필 → Σ=0·잔액 전원 일치",
"불일치 1건이라도 있으면 전체 롤백(부분 적재 금지)", "재실행 멱등
(REPLAY, 중복 분개 0)".

`related_purchase_id`·`wallet_transactions.id`는 매 테스트 실행마다 랜덤
정수를 쓴다 — 이 DB는 세션 간 초기화되지 않는 영속 테스트 DB이므로
고정 정수를 쓰면 재실행 시 `idempotency_key`가 겹쳐 이전 실행의 다른
`buyer`/`seller`와 DIGEST_MISMATCH가 난다(`test_post_entry.py`와 같은
관례로 회피).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.backfill import (
    BackfillMismatchError,
    LegacyWalletTx,
    UnrecognizedTxGroupError,
    backfill_ledger,
)
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_REFUND_RESERVE
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _IdSeq:
    """`wallet_transactions.id`(BIGSERIAL, 발생 순서) 대역 — 랜덤 시작점(재실행
    시 이전 실행 데이터와 충돌 방지) + 호출 순서대로 단조증가(백필이 이
    순서를 발생 순서로 신뢰하므로, 매 호출 독립 난수를 쓰면 순서가
    뒤섞여 중간 잔액이 음수로 떨어질 수 있다)."""

    def __init__(self) -> None:
        self._next = uuid4().int % 900_000_000 + 100_000_000

    def __call__(self) -> int:
        value = self._next
        self._next += 1
        return value


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _account_balance(pool, code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            code,
        )
    return value if value is not None else Decimal("0")


def _purchase_rows(
    ids: _IdSeq, purchase_id: int, buyer: UUID, seller: UUID,
    *, price: Decimal, commission: Decimal,
) -> list[LegacyWalletTx]:
    payout = price - commission
    return [
        LegacyWalletTx(
            id=ids(), user_id=buyer, tx_type="PURCHASE_DEBIT",
            amount=-price, related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(), user_id=seller, tx_type="SALE_CREDIT",
            amount=payout, related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(), user_id=uuid4(), tx_type="COMMISSION_CREDIT",
            amount=commission, related_purchase_id=purchase_id,
        ),
    ]


def _refund_row(ids: _IdSeq, purchase_id: int, buyer: UUID, price: Decimal) -> LegacyWalletTx:
    return LegacyWalletTx(
        id=ids(), user_id=buyer, tx_type="REFUND",
        amount=price, related_purchase_id=purchase_id,
    )


def _clawback_rows(
    ids: _IdSeq, purchase_id: int, seller: UUID,
    *, seller_share: Decimal, commission_share: Decimal,
) -> list[LegacyWalletTx]:
    return [
        LegacyWalletTx(
            id=ids(), user_id=seller, tx_type="REFUND_SELLER_CLAWBACK",
            amount=-seller_share, related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(), user_id=uuid4(), tx_type="REFUND_COMMISSION_CLAWBACK",
            amount=-commission_share, related_purchase_id=purchase_id,
        ),
    ]


async def test_backfill_topup_purchase_and_refunds_match_expected_balances(pool, ports):
    buyer, seller = uuid4(), uuid4()
    reserve_before = await _account_balance(pool, PLATFORM_REFUND_RESERVE)

    ids = _IdSeq()
    purchase_a, purchase_b = ids(), ids()
    rows = [
        LegacyWalletTx(id=ids(), user_id=buyer, tx_type="TOPUP", amount=Decimal("1000.00")),
        *_purchase_rows(
            ids, purchase_a, buyer, seller, price=Decimal("700.00"), commission=Decimal("105.00")
        ),
        # 클로백 없음 = 구 자금창출형 환불(R1)
        _refund_row(ids, purchase_a, buyer, Decimal("700.00")),
        *_purchase_rows(
            ids, purchase_b, buyer, seller, price=Decimal("400.00"), commission=Decimal("60.00")
        ),
        _refund_row(ids, purchase_b, buyer, Decimal("400.00")),
        *_clawback_rows(
            ids, purchase_b, seller,
            seller_share=Decimal("340.00"), commission_share=Decimal("60.00"),
        ),
    ]
    # 기대 잔액: buyer = 1000(topup) -700+700(구매a+환불a) -400+400(구매b+환불b) = 1000
    #           seller = 595(구매a, 소급 회수 없음) + 340 - 340(구매b, 완전 클로백) = 595
    expected = {buyer: Decimal("1000.00"), seller: Decimal("595.00")}

    async with pool.acquire() as conn, conn.transaction():
        report = await backfill_ledger(
            conn, rows, expected,
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=_clock,
        )

    # TOPUP 1 + (HOLD_PLACED+CAPTURED+PAYOUT_RELEASE)×2구매
    # + MANUAL_ADJUSTMENT(환불a) + REFUND(환불b) = 1+6+1+1 = 9
    assert report.entries_posted == 9
    assert report.accounts_verified == 2

    async with pool.acquire() as conn, conn.transaction():
        balances = await ports.balances.get_for_update(
            conn, [ua(buyer, UserSub.AVAILABLE), ua(seller, UserSub.AVAILABLE)]
        )
    assert balances[ua(buyer, UserSub.AVAILABLE)].balance == Decimal("1000.00")
    assert balances[ua(seller, UserSub.AVAILABLE)].balance == Decimal("595.00")

    reserve_after = await _account_balance(pool, PLATFORM_REFUND_RESERVE)
    assert reserve_after - reserve_before == Decimal("700.00")  # 환불a의 창출분만 흡수


async def test_backfill_rolls_back_everything_on_balance_mismatch(pool, ports):
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("50.00"))]
    wrong_expected = {buyer: Decimal("999.00")}

    with pytest.raises(BackfillMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn, rows, wrong_expected,
                journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=_clock,
            )

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
    assert found is None  # 분개는 실제로 append됐었지만(post_entry는 성공) 트랜잭션째 사라짐
    assert balance is None  # 계정 생성(ensure_user_account)까지 포함해 전부 롤백


async def test_backfill_rejects_incomplete_purchase_group(pool, ports):
    seller = uuid4()
    ids = _IdSeq()
    rows = [
        LegacyWalletTx(
            id=ids(), user_id=seller, tx_type="SALE_CREDIT",
            amount=Decimal("100.00"), related_purchase_id=ids(),
        ),
    ]
    with pytest.raises(UnrecognizedTxGroupError):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn, rows, {}, journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=_clock,
            )


async def test_backfill_rerun_is_idempotent_replay_without_duplicate_entries(pool, ports):
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("77.00"))]
    expected = {buyer: Decimal("77.00")}

    async with pool.acquire() as conn, conn.transaction():
        first = await backfill_ledger(
            conn, rows, expected,
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await backfill_ledger(
            conn, rows, expected,
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=_clock,
        )
    assert first.entries_posted == second.entries_posted == 1

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
    assert count == 1
    assert balance == Decimal("77.00")
