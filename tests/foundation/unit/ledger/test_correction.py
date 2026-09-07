"""FA-11 — correction 단위 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import PostingLine, Side
from src.foundation.ledger.domain import balance_rules
from src.foundation.ledger.domain.correction import (
    BlankReasonError,
    EmptyEntryError,
    NoOpCorrectionError,
    build_correction,
    reversal_lines,
)

_DEBIT_ACCOUNT = "PLATFORM:CASH_CLEARING"


def _lines(*, amount: Decimal, currency: Currency = Currency.KRW) -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1,
            account_code=_DEBIT_ACCOUNT,
            side=Side.DEBIT,
            amount=amount,
            currency=currency,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=amount,
            currency=currency,
        ),
    ]


def _net_delta(lines: list[PostingLine], account_code: str) -> Decimal:
    net = Decimal("0")
    for line in lines:
        if line.account_code == account_code:
            net += line.amount if line.side is Side.DEBIT else -line.amount
    return net


def test_reversal_lines_flips_every_side() -> None:
    original = _lines(amount=Decimal("100.00"))
    reversed_ = reversal_lines(original)

    assert [line.side for line in reversed_] == [Side.CREDIT, Side.DEBIT]
    assert [line.account_code for line in reversed_] == [line.account_code for line in original]
    assert [line.amount for line in reversed_] == [line.amount for line in original]


def test_reversal_lines_nets_to_zero_per_account() -> None:
    original = _lines(amount=Decimal("100.00"))
    combined = original + reversal_lines(original)

    for line in original:
        assert _net_delta(combined, line.account_code) == Decimal("0")


def test_reversal_lines_rejects_empty_input() -> None:
    with pytest.raises(EmptyEntryError):
        reversal_lines([])


def test_build_correction_bundles_reversal_and_repost() -> None:
    original_id = uuid4()
    original = _lines(amount=Decimal("100.00"))
    corrected = _lines(amount=Decimal("150.00"))

    result = build_correction(
        original_entry_id=original_id,
        original_lines=original,
        corrected_lines=corrected,
        reason="late fee waiver — audited by ops",
    )

    assert result.original_entry_id == original_id
    assert result.reason.startswith("late fee")
    assert [line.side for line in result.reversal] == [Side.CREDIT, Side.DEBIT]
    assert result.repost == tuple(corrected)


def test_build_correction_rejects_blank_reason() -> None:
    with pytest.raises(BlankReasonError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=_lines(amount=Decimal("150.00")),
            reason="   ",
        )


def test_build_correction_rejects_noop_when_digest_matches() -> None:
    original = _lines(amount=Decimal("100.00"))
    identical = [line.model_copy() for line in original]

    with pytest.raises(NoOpCorrectionError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=original,
            corrected_lines=identical,
            reason="attempted no-op",
        )


def test_build_correction_rejects_empty_corrected_lines() -> None:
    with pytest.raises(EmptyEntryError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=[],
            reason="oops",
        )


def test_build_correction_propagates_unbalanced_corrected_lines() -> None:
    unbalanced = [
        PostingLine(
            line_no=1,
            account_code=_DEBIT_ACCOUNT,
            side=Side.DEBIT,
            amount=Decimal("100.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=Decimal("90.00"),
            currency=Currency.KRW,
        ),
    ]

    with pytest.raises(balance_rules.UnbalancedEntryError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=unbalanced,
            reason="bad repost",
        )
