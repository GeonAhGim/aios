"""LC-14 `application/refund.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.
DoD(task-453): R1/R2/R3 세 환불 시나리오 각각 Σ차=Σ대 + 환불 전후 시스템
총잔액 불변을 직접 단언 + 같은 purchase 2회 환불 시 두 번째는 새 분개를
만들지 않음(LC-9 REPLAY, `ledger_journal_entry` 행 그대로 1개).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.application.purchase_flow import (
    capture_hold,
    ensure_account,
    place_hold,
)
from src.foundation.ledger.application.refund import post_refund
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.rounding import split_commission
from tests.integration.conftest import create_test_user

_TEST_PURPOSE = "TEST_REFUND_PURCHASE"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _new_purchase_id() -> int:
    return uuid4().int % 2_000_000_000


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
    """`user_wallets`(레거시 투영)와 `ledger_balance`를 처음부터 일치시킨다 —
    `place_hold`의 drift 재동기화(`purchase_flow._reconcile_available`)가
    `PLATFORM:CASH_CLEARING`을 상대 계정으로 쓰므로, 둘 중 하나만 세팅하면
    그 계정이 가짜로 마이너스가 된다(test_purchase_flow.py의 동명 헬퍼와
    동일 이유)."""
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


async def _balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _entry_lines(pool, entry_id) -> list[tuple[str, Decimal]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT side, amount FROM ledger_posting_line WHERE entry_id = $1", entry_id
        )
    return [(row["side"], row["amount"]) for row in rows]


async def _purchase(
    pool, ports, *, buyer: UUID, seller: UUID, price: Decimal, commission_rate: Decimal
) -> int:
    """place_hold + capture_hold(HOLD_CAPTURED)만 수행하고 정산(PAYOUT_RELEASE)은
    하지 않는다 — seller 판매대금이 `PENDING_PAYOUT`에 그대로 남아 R1 시나리오의
    자연스러운 전제(§4.4 "정산 창 내")가 된다. R2/R3는 이 상태에서 추가로
    정산·소비 사건을 posting해 만든다."""
    purchase_id = _new_purchase_id()
    await _seed_available(pool, buyer, price)
    reference = f"test-refund:{purchase_id}"
    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
            expires_at=_clock() + timedelta(minutes=15), actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
        await capture_hold(
            conn, hold, seller_id=seller, commission_rate=commission_rate,
            actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
    return purchase_id


async def _post(pool, ports, event: LedgerEvent) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )


async def _release_to_available(pool, ports, seller: UUID, amount: Decimal, ref: str) -> None:
    """PENDING_PAYOUT → AVAILABLE(§4.4 PAYOUT_RELEASE) — R2/R3 전제인 "정산
    창 경과"를 흉내낸다(LC-15 `application/payouts.py`가 아직 없어
    `purchase_service.py._settle`와 동일하게 직접 `post_entry`를 쓴다)."""
    async with pool.acquire() as conn:
        await ensure_account(conn, ua(seller, UserSub.AVAILABLE), Currency.KRW)
    await _post(
        pool, ports,
        LedgerEvent(
            event_type=LedgerEventType.PAYOUT_RELEASE, event_ref=ref, tenant_id=None,
            actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
            parties={"seller": seller}, extra={},
        ),
    )


async def _consume_available(pool, ports, seller: UUID, amount: Decimal, ref: str) -> None:
    """seller `AVAILABLE`을 임의 금액만큼 소비(오프플랫폼 인출)한 것으로
    흉내낸다(R3 전제 "정산금을 이미 다 씀") — §4.4 `PAYOUT_PAID`(seller
    `AVAILABLE` → `PLATFORM:PAYOUT_CLEARING`)를 그대로 쓴다. `PLATFORM:
    CASH_CLEARING`(실제 은행 입금 확인분)과 달리 `PAYOUT_CLEARING`은 이
    사건으로 잔액이 늘기만 해(§9 LC-9 docstring — credit-정상) 0에서 시작해도
    막히지 않는다."""
    await _post(
        pool, ports,
        LedgerEvent(
            event_type=LedgerEventType.PAYOUT_PAID, event_ref=ref, tenant_id=None,
            actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
            parties={"seller": seller}, extra={"external_ref": ref},
        ),
    )


def _assert_total_balance_preserved(
    *,
    price: Decimal,
    buyer_delta: Decimal,
    seller_available_delta: Decimal,
    seller_pending_delta: Decimal,
    seller_receivable_delta: Decimal,
    commission_revenue_delta: Decimal,
) -> None:
    """환불 전후 시스템 총잔액 불변(감사 §1.1 C2) — buyer가 얻은 만큼 seller의
    보유분(AVAILABLE+PENDING_PAYOUT, RECEIVABLE 증가는 그만큼의 순자산 감소이므로
    빼서 반영)과 플랫폼 커미션 수익이 정확히 줄어야 한다."""
    assert buyer_delta == price
    net_seller_and_platform = (
        seller_available_delta
        + seller_pending_delta
        - seller_receivable_delta
        + commission_revenue_delta
    )
    assert buyer_delta + net_seller_and_platform == Decimal("0")


async def test_refund_case_r1_debits_seller_pending_payout(pool, ports):
    buyer, seller = await create_test_user(pool), await create_test_user(pool)
    price = Decimal("100.00")
    rate = Decimal("0.15")
    purchase_id = await _purchase(
        pool, ports, buyer=buyer, seller=seller, price=price, commission_rate=rate
    )
    buyer_before = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    pending_before = await _balance(pool, ua(seller, UserSub.PENDING_PAYOUT))
    commission_before = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")

    async with pool.acquire() as conn, conn.transaction():
        result = await post_refund(
            conn, purchase_id=purchase_id, buyer_id=buyer, seller_id=seller, price=price,
            commission_rate=rate, admin_id=None, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
        )

    assert result.refund_case == "R1"
    lines = await _entry_lines(pool, result.entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == price  # Σ차=Σ대(직접 단언)

    buyer_after = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    pending_after = await _balance(pool, ua(seller, UserSub.PENDING_PAYOUT))
    commission_after = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")
    _assert_total_balance_preserved(
        price=price,
        buyer_delta=buyer_after - buyer_before,
        seller_available_delta=Decimal("0"),
        seller_pending_delta=pending_after - pending_before,
        seller_receivable_delta=Decimal("0"),
        commission_revenue_delta=commission_after - commission_before,
    )
    assert pending_before - pending_after == result.payout_amount


async def test_refund_case_r2_debits_seller_available(pool, ports):
    buyer, seller = await create_test_user(pool), await create_test_user(pool)
    price = Decimal("100.00")
    rate = Decimal("0.15")
    purchase_id = await _purchase(
        pool, ports, buyer=buyer, seller=seller, price=price, commission_rate=rate
    )
    _, payout = split_commission(price, rate)
    await _release_to_available(
        pool, ports, seller, payout, f"test-refund:{purchase_id}:release"
    )

    buyer_before = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    available_before = await _balance(pool, ua(seller, UserSub.AVAILABLE))
    commission_before = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")

    async with pool.acquire() as conn, conn.transaction():
        result = await post_refund(
            conn, purchase_id=purchase_id, buyer_id=buyer, seller_id=seller, price=price,
            commission_rate=rate, admin_id=None, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
        )

    assert result.refund_case == "R2"
    lines = await _entry_lines(pool, result.entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == price

    buyer_after = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    available_after = await _balance(pool, ua(seller, UserSub.AVAILABLE))
    commission_after = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")
    _assert_total_balance_preserved(
        price=price,
        buyer_delta=buyer_after - buyer_before,
        seller_available_delta=available_after - available_before,
        seller_pending_delta=Decimal("0"),
        seller_receivable_delta=Decimal("0"),
        commission_revenue_delta=commission_after - commission_before,
    )
    assert available_before - available_after == result.payout_amount


async def test_refund_case_r3_splits_available_and_receivable(pool, ports):
    buyer, seller = await create_test_user(pool), await create_test_user(pool)
    price = Decimal("100.00")
    rate = Decimal("0.15")
    purchase_id = await _purchase(
        pool, ports, buyer=buyer, seller=seller, price=price, commission_rate=rate
    )
    _, payout = split_commission(price, rate)
    await _release_to_available(
        pool, ports, seller, payout, f"test-refund:{purchase_id}:release"
    )
    # seller가 정산금 대부분을 이미 소비 — payout(85.00)의 40.00만 남긴다.
    remaining = Decimal("40.00")
    await _consume_available(
        pool, ports, seller, payout - remaining, f"test-refund:{purchase_id}:spend"
    )

    buyer_before = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    available_before = await _balance(pool, ua(seller, UserSub.AVAILABLE))
    receivable_before = await _balance(pool, ua(seller, UserSub.RECEIVABLE))
    commission_before = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")
    assert available_before == remaining

    async with pool.acquire() as conn, conn.transaction():
        result = await post_refund(
            conn, purchase_id=purchase_id, buyer_id=buyer, seller_id=seller, price=price,
            commission_rate=rate, admin_id=None, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
        )

    assert result.refund_case == "R3"
    lines = await _entry_lines(pool, result.entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == price

    buyer_after = await _balance(pool, ua(buyer, UserSub.AVAILABLE))
    available_after = await _balance(pool, ua(seller, UserSub.AVAILABLE))
    receivable_after = await _balance(pool, ua(seller, UserSub.RECEIVABLE))
    commission_after = await _balance(pool, "PLATFORM:COMMISSION_REVENUE")

    assert available_after == Decimal("0.00")  # 가용분 전액 소진
    shortfall = result.payout_amount - remaining
    assert receivable_after - receivable_before == shortfall  # 부족분은 대손성 채권으로

    _assert_total_balance_preserved(
        price=price,
        buyer_delta=buyer_after - buyer_before,
        seller_available_delta=available_after - available_before,
        seller_pending_delta=Decimal("0"),
        seller_receivable_delta=receivable_after - receivable_before,
        commission_revenue_delta=commission_after - commission_before,
    )


async def test_refund_duplicate_purchase_id_is_not_double_posted(pool, ports):
    """이중 환불 거부(DoD) — 같은 `purchase_id`로 두 번째 호출은 새 분개를
    만들지 않는다(LC-9 REPLAY). PENDING_PAYOUT을 payout의 2배로 만들어 첫
    호출 뒤에도 여전히 R1 조건을 만족시킴으로써 두 호출의 분개행이 정확히
    같아(같은 digest) REPLAY로 확정되게 한다 — 그렇지 않으면(예: 잔액이
    두 호출 사이에 달라져 케이스가 바뀌면) DIGEST_MISMATCH(409)로 거부되는데,
    그 경우도 "두 번째는 새 분개를 만들지 않는다"는 이 테스트의 핵심 불변은
    동일하게 지켜진다."""
    buyer1, buyer2, seller = (
        await create_test_user(pool), await create_test_user(pool), await create_test_user(pool)
    )
    price = Decimal("100.00")
    rate = Decimal("0.15")
    purchase_id = await _purchase(
        pool, ports, buyer=buyer1, seller=seller, price=price, commission_rate=rate
    )
    await _purchase(pool, ports, buyer=buyer2, seller=seller, price=price, commission_rate=rate)

    async def _refund():
        async with pool.acquire() as conn, conn.transaction():
            return await post_refund(
                conn, purchase_id=purchase_id, buyer_id=buyer1, seller_id=seller, price=price,
                commission_rate=rate, admin_id=None, trace_id=uuid4(),
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock,
            )

    first = await _refund()
    assert first.refund_case == "R1"
    assert first.entry.replayed is False

    second = await _refund()
    assert second.entry.entry_id == first.entry.entry_id
    assert second.entry.replayed is True

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"refund:purchase:{purchase_id}",
        )
    assert count == 1
