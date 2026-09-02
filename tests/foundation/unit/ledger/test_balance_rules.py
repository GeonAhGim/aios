"""LC-3 — balance_rules 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-3
("음수/초과 인출 거부: allow_negative=False면 balance-held<0 금지",
negative: "held>balance").
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import PostingLine, Side
from src.foundation.ledger.domain import balance_rules


def _lines(
    *, debit: Decimal, credit: Decimal, currency: Currency = Currency.KRW
) -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=debit,
            currency=currency,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=credit,
            currency=currency,
        ),
    ]


def _balance(
    *, balance: Decimal, held: Decimal = Decimal("0"), allow_negative: bool = False
) -> balance_rules.Balance:
    return balance_rules.Balance(
        account_code=f"USER:{uuid4()}:AVAILABLE",
        balance=balance,
        held=held,
        currency=Currency.KRW,
        allow_negative=allow_negative,
        last_entry_seq=1,
    )


def test_check_balanced_accepts_equal_debit_credit() -> None:
    balance_rules.check_balanced(_lines(debit=Decimal("100.00"), credit=Decimal("100.00")))


def test_check_balanced_rejects_unequal_sums() -> None:
    with pytest.raises(balance_rules.UnbalancedEntryError):
        balance_rules.check_balanced(_lines(debit=Decimal("100.00"), credit=Decimal("99.00")))


def test_check_balanced_rejects_empty_lines() -> None:
    with pytest.raises(balance_rules.UnbalancedEntryError):
        balance_rules.check_balanced([])


def test_check_balanced_rejects_mixed_currency() -> None:
    lines = [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=Decimal("100.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=Decimal("100.00"),
            currency=Currency.USDT,
        ),
    ]
    with pytest.raises(balance_rules.CurrencyMismatchError):
        balance_rules.check_balanced(lines)


def test_apply_updates_balance_and_held() -> None:
    bal = _balance(balance=Decimal("500.00"), held=Decimal("0"))
    updated = balance_rules.apply(
        bal, delta_balance=Decimal("-100.00"), delta_held=Decimal("100.00"), entry_seq=2
    )
    assert updated.balance == Decimal("400.00")
    assert updated.held == Decimal("100.00")
    assert updated.last_entry_seq == 2
    # 원본은 불변(frozen dataclass) — apply는 항상 새 값을 반환한다.
    assert bal.balance == Decimal("500.00")


def test_apply_rejects_held_exceeding_balance_when_negative_disallowed() -> None:
    """held > balance → available(balance-held) < 0 → allow_negative=False면 거부."""
    bal = _balance(balance=Decimal("100.00"), held=Decimal("0"), allow_negative=False)
    with pytest.raises(balance_rules.InsufficientAvailableError):
        balance_rules.apply(bal, delta_held=Decimal("150.00"), entry_seq=2)


def test_apply_rejects_negative_held_regardless_of_allow_negative() -> None:
    bal = _balance(balance=Decimal("100.00"), held=Decimal("50.00"), allow_negative=True)
    with pytest.raises(balance_rules.InsufficientAvailableError):
        balance_rules.apply(bal, delta_held=Decimal("-100.00"), entry_seq=2)


def test_apply_allows_negative_available_for_receivable_account() -> None:
    """RECEIVABLE류(allow_negative=True) 계정은 balance-held<0이어도 허용."""
    bal = _balance(balance=Decimal("0"), held=Decimal("0"), allow_negative=True)
    updated = balance_rules.apply(bal, delta_balance=Decimal("-300.00"), entry_seq=2)
    assert updated.balance == Decimal("-300.00")
