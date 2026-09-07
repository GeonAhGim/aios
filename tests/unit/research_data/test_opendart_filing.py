"""RD-20 — `domain/corporate_action/opendart_filing.py` 순수 규칙 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 실제 공시 유형 3종(액면분할·현금배당·합병) 정규화가 정확함 + 파싱
실패는 조용히 무시되지 않고 `FilingParseError`로 알려짐(적대적 입력 포함).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.domain.corporate_action.opendart_filing import (
    FilingParseError,
    OpenDartFiling,
    normalize_filing,
)

_KNOWN_AT = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


def test_split_filing_normalizes_ratio_from_par_value_change() -> None:
    # 액면분할결정: 액면가 5,000원 -> 500원 (10:1 분할, ratio=10)
    instrument_id = uuid4()
    filing = OpenDartFiling(
        instrument_id=instrument_id,
        rcept_no="20260302000123",
        report_type="SPLIT",
        event_date=date(2026, 4, 1),
        known_at=_KNOWN_AT,
        split_ratio_before=Decimal("5000"),
        split_ratio_after=Decimal("500"),
    )

    action = normalize_filing(filing)

    assert action.action_type == "SPLIT"
    assert action.instrument_id == instrument_id
    assert action.ex_date == date(2026, 4, 1)
    assert action.ratio == Decimal("10")
    assert action.cash_amount is None
    assert action.source_ref == "20260302000123"
    assert action.known_at == _KNOWN_AT


def test_cash_dividend_filing_normalizes_ratio_one_and_cash_amount() -> None:
    # 현금배당결정: 1주당 750원
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000456",
        report_type="CASH_DIVIDEND",
        event_date=date(2026, 3, 31),
        known_at=_KNOWN_AT,
        dividend_per_share=Decimal("750"),
    )

    action = normalize_filing(filing)

    assert action.action_type == "CASH_DIVIDEND"
    assert action.ratio == Decimal("1")
    assert action.cash_amount == Decimal("750")


def test_merger_filing_normalizes_given_conversion_ratio() -> None:
    # 합병결정: 합병비율 1 : 0.5
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000789",
        report_type="MERGER",
        event_date=date(2026, 6, 1),
        known_at=_KNOWN_AT,
        merger_ratio=Decimal("0.5"),
    )

    action = normalize_filing(filing)

    assert action.action_type == "MERGER"
    assert action.ratio == Decimal("0.5")
    assert action.cash_amount is None


def test_split_filing_missing_ratio_fields_raises_parse_error() -> None:
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000999",
        report_type="SPLIT",
        event_date=date(2026, 4, 1),
        known_at=_KNOWN_AT,
    )

    with pytest.raises(FilingParseError):
        normalize_filing(filing)


def test_cash_dividend_zero_amount_raises_parse_error() -> None:
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302001000",
        report_type="CASH_DIVIDEND",
        event_date=date(2026, 3, 31),
        known_at=_KNOWN_AT,
        dividend_per_share=Decimal("0"),
    )

    with pytest.raises(FilingParseError):
        normalize_filing(filing)


def test_naive_known_at_raises_parse_error() -> None:
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302001111",
        report_type="MERGER",
        event_date=date(2026, 6, 1),
        known_at=datetime(2026, 3, 2, 9, 0),  # tz 없음
        merger_ratio=Decimal("0.5"),
    )

    with pytest.raises(FilingParseError):
        normalize_filing(filing)
