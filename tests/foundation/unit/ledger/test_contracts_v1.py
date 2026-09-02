"""LC-1 — ledger/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3 (C), §9 LC-1.

`fixtures/ledger_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거 시
실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를
함께 갱신한다.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency
from src.foundation.ledger.contracts import v1

FIXTURE = Path(__file__).parent / "fixtures" / "ledger_contracts_v1.json"

_MODELS = (
    v1.LedgerEvent,
    v1.PostingLine,
    v1.JournalEntryView,
    v1.BalanceView,
    v1.HoldView,
    v1.PayoutBatchView,
    v1.TrialBalanceView,
    v1.IntegrityReport,
)


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _sample_event(**overrides: object) -> v1.LedgerEvent:
    base: dict[str, object] = dict(
        event_type=v1.LedgerEventType.TOPUP_CONFIRMED,
        event_ref="topup:1",
        tenant_id=uuid4(),
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        amount=Decimal("1000"),
        currency=Currency.KRW,
        parties={"user": uuid4()},
    )
    base.update(overrides)
    return v1.LedgerEvent(**base)  # type: ignore[arg-type]


def _sample_line(**overrides: object) -> v1.PostingLine:
    base: dict[str, object] = dict(
        line_no=1,
        account_code="PLATFORM:CASH_CLEARING",
        side=v1.Side.DEBIT,
        amount=Decimal("100.00"),
        currency=Currency.KRW,
    )
    base.update(overrides)
    return v1.PostingLine(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_ledger_event_amount_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_event(amount=Decimal("0"))


def test_ledger_event_amount_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_event(amount=Decimal("-1"))


def test_posting_line_amount_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_line(amount=Decimal("0"))


def test_posting_line_amount_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_line(amount=Decimal("-100.00"))


def test_posting_line_amount_over_two_decimal_places_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_line(amount=Decimal("100.001"))


def test_journal_entry_view_naive_posted_at_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.JournalEntryView(
            entry_id=uuid4(),
            sequence_no=1,
            event_type=v1.LedgerEventType.TOPUP_CONFIRMED,
            event_ref="topup:1",
            idempotency_key="TOPUP_CONFIRMED:topup:1",
            lines=[_sample_line()],
            lines_digest="d" * 64,
            prev_hash=None,
            entry_hash="e" * 64,
            audit_event_id=uuid4(),
            posted_at=datetime(2026, 9, 3, 0, 0),
        )


def test_journal_entry_view_accepts_aware_posted_at() -> None:
    entry = v1.JournalEntryView(
        entry_id=uuid4(),
        sequence_no=1,
        event_type=v1.LedgerEventType.TOPUP_CONFIRMED,
        event_ref="topup:1",
        idempotency_key="TOPUP_CONFIRMED:topup:1",
        lines=[_sample_line()],
        lines_digest="d" * 64,
        prev_hash=None,
        entry_hash="e" * 64,
        audit_event_id=uuid4(),
        posted_at=_now(),
    )
    assert entry.replayed is False
    assert entry.schema_version == "v1"


def test_balance_view_naive_as_of_rejected() -> None:
    with pytest.raises(ValidationError):
        v1.BalanceView(
            account_code=f"USER:{uuid4()}:AVAILABLE",
            balance=Decimal("0"),
            held=Decimal("0"),
            available=Decimal("0"),
            pending_payout=Decimal("0"),
            currency=Currency.KRW,
            last_entry_seq=0,
            as_of=datetime(2026, 9, 3, 0, 0),
        )


def test_trial_balance_total_zero_roundtrip() -> None:
    tb = v1.TrialBalanceView(
        as_of=_now(),
        last_entry_seq=5,
        balances={
            "PLATFORM:CASH_CLEARING": Decimal("100.00"),
            "PLATFORM:COMMISSION_REVENUE": Decimal("-100.00"),
        },
        total=Decimal("0"),
    )
    assert sum(tb.balances.values()) == Decimal("0")
    assert tb.total == Decimal("0")


def test_payout_batch_view_state_literal_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        v1.PayoutBatchView(
            batch_id=uuid4(),
            seller_user_id=uuid4(),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            amount=Decimal("500.00"),
            state="UNKNOWN",  # type: ignore[arg-type]
            capture_entry_ids=[],
            release_entry_id=None,
            paid_entry_id=None,
        )


def test_integrity_report_drift_tuple_roundtrip() -> None:
    report = v1.IntegrityReport(
        checked_at=_now(),
        entries_verified=10,
        chain_ok=True,
        zero_sum_ok=True,
        drifts=[("PLATFORM:CASH_CLEARING", Decimal("1.00"), Decimal("2.00"))],
        first_broken_seq=None,
    )
    assert report.drifts[0][0] == "PLATFORM:CASH_CLEARING"
