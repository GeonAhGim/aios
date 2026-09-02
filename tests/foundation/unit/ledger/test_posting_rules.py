"""LC-4 — posting_rules 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-4
("사건 9종 × Σ차=Σ대, 필수 parties 누락 → 예외, R1/R2/R3 분기").
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, Side, UserSub
from src.foundation.ledger.domain import balance_rules, posting_rules
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_PAYOUT_CLEARING,
    InvalidAccountCodeError,
    user_account,
)


def _event(
    event_type: LedgerEventType,
    *,
    amount: Decimal = Decimal("10000.00"),
    currency: Currency = Currency.KRW,
    parties: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        event_ref=f"{event_type.value.lower()}:1",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=currency,
        parties=parties or {},
        extra=extra or {},
    )


def _assert_zero_sum(lines: list) -> None:
    debit = sum((line.amount for line in lines if line.side is Side.DEBIT), Decimal("0"))
    credit = sum((line.amount for line in lines if line.side is Side.CREDIT), Decimal("0"))
    assert debit == credit
    assert len({line.currency for line in lines}) == 1
    # 순수성: 반환된 목록에 재검증을 다시 돌려도 예외가 나지 않는다(멱등적 순수 함수).
    balance_rules.check_balanced(lines)


def test_topup_confirmed_balances() -> None:
    user_id = uuid4()
    event = _event(LedgerEventType.TOPUP_CONFIRMED, parties={"user": user_id})
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    debit = next(line for line in lines if line.side is Side.DEBIT)
    credit = next(line for line in lines if line.side is Side.CREDIT)
    assert debit.account_code == PLATFORM_CASH_CLEARING
    assert credit.account_code == user_account(user_id, UserSub.AVAILABLE)


def test_hold_placed_balances() -> None:
    buyer_id = uuid4()
    event = _event(LedgerEventType.HOLD_PLACED, parties={"buyer": buyer_id})
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    debit = next(line for line in lines if line.side is Side.DEBIT)
    credit = next(line for line in lines if line.side is Side.CREDIT)
    assert debit.account_code == user_account(buyer_id, UserSub.AVAILABLE)
    assert credit.account_code == user_account(buyer_id, UserSub.HELD)


def test_hold_captured_splits_commission_and_balances() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.HOLD_CAPTURED,
        amount=Decimal("10000.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={"commission_rate": Decimal("0.15")},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    by_account = {line.account_code: line for line in lines}
    assert by_account[user_account(buyer_id, UserSub.HELD)].side is Side.DEBIT
    assert by_account[user_account(buyer_id, UserSub.HELD)].amount == Decimal("10000.00")
    assert by_account[user_account(seller_id, UserSub.PENDING_PAYOUT)].amount == Decimal("8500.00")
    assert by_account[PLATFORM_COMMISSION_REVENUE].amount == Decimal("1500.00")


def test_hold_captured_full_commission_omits_zero_payout_line() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.HOLD_CAPTURED,
        amount=Decimal("100.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={"commission_rate": Decimal("1")},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    assert len(lines) == 2
    codes = {line.account_code for line in lines}
    assert user_account(seller_id, UserSub.PENDING_PAYOUT) not in codes


def test_hold_released_balances() -> None:
    buyer_id = uuid4()
    event = _event(LedgerEventType.HOLD_RELEASED, parties={"buyer": buyer_id})
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    debit = next(line for line in lines if line.side is Side.DEBIT)
    assert debit.account_code == user_account(buyer_id, UserSub.HELD)


def test_payout_release_balances() -> None:
    seller_id = uuid4()
    event = _event(LedgerEventType.PAYOUT_RELEASE, parties={"seller": seller_id})
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    debit = next(line for line in lines if line.side is Side.DEBIT)
    assert debit.account_code == user_account(seller_id, UserSub.PENDING_PAYOUT)


def test_payout_paid_balances_and_requires_external_ref() -> None:
    seller_id = uuid4()
    event = _event(
        LedgerEventType.PAYOUT_PAID,
        parties={"seller": seller_id},
        extra={"external_ref": "bank:tx-1"},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    credit = next(line for line in lines if line.side is Side.CREDIT)
    assert credit.account_code == PLATFORM_PAYOUT_CLEARING


def test_refund_r1_debits_pending_payout_and_commission() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.REFUND,
        amount=Decimal("10000.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={"commission_rate": Decimal("0.15"), "refund_case": "R1"},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    by_account = {line.account_code: line for line in lines}
    assert by_account[user_account(seller_id, UserSub.PENDING_PAYOUT)].side is Side.DEBIT
    assert by_account[user_account(seller_id, UserSub.PENDING_PAYOUT)].amount == Decimal("8500.00")
    assert by_account[PLATFORM_COMMISSION_REVENUE].side is Side.DEBIT
    assert by_account[user_account(buyer_id, UserSub.AVAILABLE)].side is Side.CREDIT
    assert by_account[user_account(buyer_id, UserSub.AVAILABLE)].amount == Decimal("10000.00")


def test_refund_r2_debits_seller_available() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.REFUND,
        amount=Decimal("10000.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={"commission_rate": Decimal("0.15"), "refund_case": "R2"},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    by_account = {line.account_code: line for line in lines}
    assert by_account[user_account(seller_id, UserSub.AVAILABLE)].side is Side.DEBIT
    assert by_account[user_account(seller_id, UserSub.AVAILABLE)].amount == Decimal("8500.00")


def test_refund_r3_splits_available_and_receivable_shortfall() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.REFUND,
        amount=Decimal("10000.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={
            "commission_rate": Decimal("0.15"),
            "refund_case": "R3",
            "seller_available_amount": Decimal("3000.00"),
        },
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    by_account = {line.account_code: line for line in lines}
    assert by_account[user_account(seller_id, UserSub.AVAILABLE)].amount == Decimal("3000.00")
    assert by_account[user_account(seller_id, UserSub.RECEIVABLE)].amount == Decimal("5500.00")
    assert by_account[PLATFORM_COMMISSION_REVENUE].amount == Decimal("1500.00")


def test_refund_r3_no_shortfall_omits_receivable_line() -> None:
    buyer_id, seller_id = uuid4(), uuid4()
    event = _event(
        LedgerEventType.REFUND,
        amount=Decimal("10000.00"),
        parties={"buyer": buyer_id, "seller": seller_id},
        extra={
            "commission_rate": Decimal("0.15"),
            "refund_case": "R3",
            "seller_available_amount": Decimal("999999.00"),
        },
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    assert user_account(seller_id, UserSub.RECEIVABLE) not in {line.account_code for line in lines}


def test_chargeback_splits_available_and_receivable() -> None:
    user_id = uuid4()
    event = _event(
        LedgerEventType.CHARGEBACK,
        amount=Decimal("5000.00"),
        parties={"user": user_id},
        extra={"user_available_amount": Decimal("2000.00")},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    by_account = {line.account_code: line for line in lines}
    assert by_account[user_account(user_id, UserSub.AVAILABLE)].amount == Decimal("2000.00")
    assert by_account[user_account(user_id, UserSub.RECEIVABLE)].amount == Decimal("3000.00")
    assert by_account[PLATFORM_CASH_CLEARING].side is Side.CREDIT


def test_chargeback_fully_covered_omits_receivable_line() -> None:
    user_id = uuid4()
    event = _event(
        LedgerEventType.CHARGEBACK,
        amount=Decimal("5000.00"),
        parties={"user": user_id},
        extra={"user_available_amount": Decimal("999999.00")},
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)
    assert len(lines) == 2
    assert user_account(user_id, UserSub.RECEIVABLE) not in {line.account_code for line in lines}


def test_manual_adjustment_balances() -> None:
    event = _event(
        LedgerEventType.MANUAL_ADJUSTMENT,
        amount=Decimal("42.00"),
        extra={
            "debit_account": PLATFORM_CASH_CLEARING,
            "credit_account": PLATFORM_COMMISSION_REVENUE,
        },
    )
    lines = posting_rules.lines_for(event)
    _assert_zero_sum(lines)


# --- negative ---


def test_missing_required_party_raises() -> None:
    event = _event(LedgerEventType.HOLD_PLACED, parties={})
    with pytest.raises(posting_rules.MissingPartyError):
        posting_rules.lines_for(event)


def test_missing_required_extra_raises() -> None:
    event = _event(
        LedgerEventType.HOLD_CAPTURED,
        parties={"buyer": uuid4(), "seller": uuid4()},
        extra={},
    )
    with pytest.raises(posting_rules.MissingExtraFieldError):
        posting_rules.lines_for(event)


def test_refund_invalid_case_raises() -> None:
    event = _event(
        LedgerEventType.REFUND,
        parties={"buyer": uuid4(), "seller": uuid4()},
        extra={"commission_rate": Decimal("0.15"), "refund_case": "R9"},
    )
    with pytest.raises(posting_rules.MissingExtraFieldError):
        posting_rules.lines_for(event)


def test_unsupported_event_type_raises() -> None:
    """스키마 검증을 우회해 9종 밖의 event_type을 주입한다(방어 코드 경로 확인)."""
    bogus = LedgerEvent.model_construct(
        event_type="NOT_A_REAL_EVENT",
        event_ref="x:1",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("1.00"),
        currency=Currency.KRW,
        parties={},
        extra={},
        schema_version="v1",
    )
    with pytest.raises(posting_rules.UnsupportedEventTypeError):
        posting_rules.lines_for(bogus)


def test_amount_not_positive_rejected_at_event_construction() -> None:
    with pytest.raises(ValidationError):
        _event(LedgerEventType.TOPUP_CONFIRMED, amount=Decimal("0"), parties={"user": uuid4()})


def test_manual_adjustment_mixed_currency_rejected() -> None:
    """수동조정 두 행에 다른 통화를 주면 LC-3(check_balanced)이 거부한다(재사용)."""
    event = _event(
        LedgerEventType.MANUAL_ADJUSTMENT,
        amount=Decimal("10.00"),
        currency=Currency.KRW,
        extra={
            "debit_account": PLATFORM_CASH_CLEARING,
            "credit_account": PLATFORM_COMMISSION_REVENUE,
            "credit_currency": "USDT",
        },
    )
    with pytest.raises(balance_rules.CurrencyMismatchError):
        posting_rules.lines_for(event)


def test_manual_adjustment_unknown_account_rejected() -> None:
    """알 수 없는 PLATFORM 계정명은 LC-2(account_type)가 유형 판정에 실패한다."""
    event = _event(
        LedgerEventType.MANUAL_ADJUSTMENT,
        amount=Decimal("10.00"),
        extra={
            "debit_account": "PLATFORM:NOT_A_REAL_ACCOUNT",
            "credit_account": PLATFORM_COMMISSION_REVENUE,
        },
    )
    with pytest.raises(InvalidAccountCodeError):
        posting_rules.lines_for(event)
