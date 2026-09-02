"""LC-5 — payout_schedule 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-5
("창 미경과 제외, 판매자별 합산, cutoff 경계",
negative: "통화 혼합, 중복 payout 키").
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.domain import payout_schedule as ps

_WINDOW = timedelta(days=3)
_PERIOD_START = date(2026, 9, 1)
_PERIOD_END = date(2026, 9, 8)


def _capture(
    *,
    seller: object | None = None,
    amount: Decimal = Decimal("1000.00"),
    currency: Currency = Currency.KRW,
    captured_at: datetime,
) -> ps.CaptureRecord:
    return ps.CaptureRecord(
        entry_id=uuid4(),
        seller_user_id=seller or uuid4(),
        amount=amount,
        currency=currency,
        captured_at=captured_at,
    )


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


def _schedule(captures: list[ps.CaptureRecord], *, now: datetime) -> list[ps.PayoutScheduleItem]:
    return ps.schedule_payouts(
        captures,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        settlement_window=_WINDOW,
        now=now,
    )


def test_window_not_elapsed_is_excluded() -> None:
    seller = uuid4()
    captured_at = _dt(5)
    capture = _capture(seller=seller, captured_at=captured_at)
    items = _schedule([capture], now=captured_at + timedelta(days=1))
    assert items == []


def test_window_elapsed_is_included() -> None:
    seller = uuid4()
    captured_at = _dt(5)
    capture = _capture(seller=seller, amount=Decimal("1234.50"), captured_at=captured_at)
    items = _schedule([capture], now=captured_at + _WINDOW)
    assert len(items) == 1
    assert items[0].seller_user_id == seller
    assert items[0].amount == Decimal("1234.50")
    assert items[0].capture_entry_ids == [capture.entry_id]


def test_cutoff_boundary_now_equal_to_window_included() -> None:
    """now == captured_at + window는 "경과"로 취급(포함)."""
    captured_at = _dt(2)
    capture = _capture(captured_at=captured_at)
    items = _schedule([capture], now=captured_at + _WINDOW)
    assert len(items) == 1


def test_cutoff_boundary_one_second_before_window_excluded() -> None:
    captured_at = _dt(2)
    capture = _capture(captured_at=captured_at)
    items = _schedule([capture], now=captured_at + _WINDOW - timedelta(seconds=1))
    assert items == []


def test_outside_period_excluded_even_if_window_elapsed() -> None:
    captured_at = datetime(2026, 8, 20, tzinfo=timezone.utc)  # period 밖
    capture = _capture(captured_at=captured_at)
    items = _schedule([capture], now=captured_at + timedelta(days=30))
    assert items == []


def test_aggregates_by_seller() -> None:
    seller_a, seller_b = uuid4(), uuid4()
    captured_at = _dt(3)
    now = captured_at + _WINDOW
    captures = [
        _capture(seller=seller_a, amount=Decimal("100.00"), captured_at=captured_at),
        _capture(seller=seller_a, amount=Decimal("50.00"), captured_at=captured_at),
        _capture(seller=seller_b, amount=Decimal("30.00"), captured_at=captured_at),
    ]
    items = _schedule(captures, now=now)
    by_seller = {item.seller_user_id: item for item in items}
    assert by_seller[seller_a].amount == Decimal("150.00")
    assert len(by_seller[seller_a].capture_entry_ids) == 2
    assert by_seller[seller_b].amount == Decimal("30.00")


def test_batch_key_is_seller_and_period_end() -> None:
    seller = uuid4()
    captured_at = _dt(3)
    capture = _capture(seller=seller, captured_at=captured_at)
    items = _schedule([capture], now=captured_at + _WINDOW)
    assert items[0].batch_key == ps.batch_key(seller, _PERIOD_END)


# --- negative ---


def test_zero_amount_capture_rejected() -> None:
    with pytest.raises(ps.InvalidCaptureAmountError):
        ps.CaptureRecord(
            entry_id=uuid4(),
            seller_user_id=uuid4(),
            amount=Decimal("0"),
            currency=Currency.KRW,
            captured_at=_dt(1),
        )


def test_negative_amount_capture_rejected() -> None:
    with pytest.raises(ps.InvalidCaptureAmountError):
        ps.CaptureRecord(
            entry_id=uuid4(),
            seller_user_id=uuid4(),
            amount=Decimal("-1.00"),
            currency=Currency.KRW,
            captured_at=_dt(1),
        )


def test_mixed_currency_for_same_seller_rejected() -> None:
    seller = uuid4()
    captured_at = _dt(3)
    now = captured_at + _WINDOW
    captures = [
        _capture(seller=seller, currency=Currency.KRW, captured_at=captured_at),
        _capture(seller=seller, currency=Currency.USDT, captured_at=captured_at),
    ]
    with pytest.raises(ps.MixedCurrencyBatchError):
        _schedule(captures, now=now)


def test_duplicate_capture_entry_id_rejected() -> None:
    captured_at = _dt(3)
    capture = _capture(captured_at=captured_at)
    with pytest.raises(ps.DuplicateCaptureError):
        _schedule([capture, capture], now=captured_at + _WINDOW)
