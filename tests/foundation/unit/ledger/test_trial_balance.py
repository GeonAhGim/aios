"""LC-5 — trial_balance 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-5
("합계 보존 증명: 무작위 사건열 1,000건 fold → Σ=0 항상; 각 사건 후에도 Σ=0").
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, PostingLine, Side
from src.foundation.ledger.domain import posting_rules, trial_balance
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_PAYOUT_CLEARING,
    PLATFORM_REFUND_RESERVE,
)

_SEED = 310
_N_EVENTS = 1000
_PLATFORM_ACCOUNTS = [
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_REFUND_RESERVE,
    PLATFORM_PAYOUT_CLEARING,
]


def _rand_amount(rng: random.Random) -> Decimal:
    cents = rng.randint(1, 10_000_000)
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def _rand_rate(rng: random.Random) -> Decimal:
    return (Decimal(rng.randint(0, 100)) / 100).quantize(Decimal("0.01"))


def _make_event(rng: random.Random, event_type: LedgerEventType) -> LedgerEvent:
    amount = _rand_amount(rng)
    common = dict(
        event_type=event_type,
        event_ref=f"{event_type.value.lower()}:{rng.randint(1, 10**9)}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=Currency.KRW,
    )

    if event_type is LedgerEventType.TOPUP_CONFIRMED:
        return LedgerEvent(**common, parties={"user": uuid4()})
    if event_type is LedgerEventType.HOLD_PLACED:
        return LedgerEvent(**common, parties={"buyer": uuid4()})
    if event_type is LedgerEventType.HOLD_CAPTURED:
        return LedgerEvent(
            **common,
            parties={"buyer": uuid4(), "seller": uuid4()},
            extra={"commission_rate": _rand_rate(rng)},
        )
    if event_type is LedgerEventType.HOLD_RELEASED:
        return LedgerEvent(**common, parties={"buyer": uuid4()})
    if event_type is LedgerEventType.REFUND:
        case = rng.choice(["R1", "R2", "R3"])
        extra: dict[str, Decimal | str] = {"commission_rate": _rand_rate(rng), "refund_case": case}
        if case == "R3":
            extra["seller_available_amount"] = _rand_amount(rng)
        return LedgerEvent(**common, parties={"buyer": uuid4(), "seller": uuid4()}, extra=extra)
    if event_type is LedgerEventType.CHARGEBACK:
        return LedgerEvent(
            **common,
            parties={"user": uuid4()},
            extra={"user_available_amount": _rand_amount(rng)},
        )
    if event_type is LedgerEventType.PAYOUT_RELEASE:
        return LedgerEvent(**common, parties={"seller": uuid4()})
    if event_type is LedgerEventType.PAYOUT_PAID:
        return LedgerEvent(
            **common,
            parties={"seller": uuid4()},
            extra={"external_ref": f"bank:{rng.randint(1, 10**9)}"},
        )
    if event_type is LedgerEventType.MANUAL_ADJUSTMENT:
        debit_account, credit_account = rng.sample(_PLATFORM_ACCOUNTS, 2)
        return LedgerEvent(
            **common,
            parties={},
            extra={"debit_account": debit_account, "credit_account": credit_account},
        )
    raise AssertionError(f"generator not defined for {event_type}")


def _generate_lines(seed: int, n: int) -> list[list[PostingLine]]:
    rng = random.Random(seed)
    event_types = list(LedgerEventType)
    return [posting_rules.lines_for(_make_event(rng, rng.choice(event_types))) for _ in range(n)]


def test_1000_random_events_preserve_zero_sum_total() -> None:
    entries = _generate_lines(_SEED, _N_EVENTS)
    balances = trial_balance.build_trial_balance(entries)
    assert trial_balance.total(balances) == Decimal("0")
    trial_balance.verify_zero_sum(balances)  # 예외 없이 통과


def test_1000_random_events_zero_sum_holds_after_every_single_event() -> None:
    entries = _generate_lines(_SEED, _N_EVENTS)
    balances: dict[str, Decimal] = {}
    for i, lines in enumerate(entries):
        balances = trial_balance.apply_entry(balances, lines)
        assert trial_balance.total(balances) == Decimal("0"), f"사건 {i}번째 이후 Σ != 0"


def test_to_view_snapshots_balanced_state() -> None:
    entries = _generate_lines(_SEED, 50)
    balances = trial_balance.build_trial_balance(entries)
    view = trial_balance.to_view(
        balances, as_of=datetime(2026, 9, 3, tzinfo=timezone.utc), last_entry_seq=50
    )
    assert view.total == Decimal("0")
    assert view.balances == balances


def test_apply_entry_does_not_mutate_input() -> None:
    lines = [
        PostingLine(
            line_no=1,
            account_code=PLATFORM_CASH_CLEARING,
            side=Side.DEBIT,
            amount=Decimal("10.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code=PLATFORM_COMMISSION_REVENUE,
            side=Side.CREDIT,
            amount=Decimal("10.00"),
            currency=Currency.KRW,
        ),
    ]
    original: dict[str, Decimal] = {}
    updated = trial_balance.apply_entry(original, lines)
    assert original == {}
    assert updated[PLATFORM_CASH_CLEARING] == Decimal("10.00")


# --- negative ---


def test_verify_zero_sum_rejects_nonzero_total() -> None:
    balances = {PLATFORM_CASH_CLEARING: Decimal("100.00")}
    with pytest.raises(trial_balance.TrialBalanceNonZeroError) as exc_info:
        trial_balance.verify_zero_sum(balances)
    assert exc_info.value.total == Decimal("100.00")
    assert exc_info.value.balances == balances


def test_to_view_raises_and_does_not_build_view_when_unbalanced() -> None:
    balances = {PLATFORM_CASH_CLEARING: Decimal("1.00")}
    with pytest.raises(trial_balance.TrialBalanceNonZeroError):
        trial_balance.to_view(
            balances, as_of=datetime(2026, 9, 3, tzinfo=timezone.utc), last_entry_seq=1
        )
