"""BT-20 분할·배당 조정 — 2:1 분할 픽스처 비율 검증·미조정 플래그·negative.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.4(`adjustments{splits, dividends}`), ADR-2026-09-06-G 135행(BT-20).

모든 기대값은 손으로 계산해 Decimal exact 비교로 단언한다(float 근사
비교 금지).
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.domain.corporate_actions import (
    AdjustedFill,
    CashDividend,
    StockSplit,
    adjust_fill,
    adjust_price_for_dividends,
    adjust_price_for_splits,
    adjust_quantity_for_splits,
    dividend_factor,
    split_factor,
)
from src.foundation.backtest.domain.models_v2 import AdjustmentsConfig

_ADJ_ON = AdjustmentsConfig(splits=True, dividends=True)
_ADJ_OFF = AdjustmentsConfig(splits=False, dividends=False)

_BEFORE = datetime(2026, 2, 1, tzinfo=timezone.utc)
_SPLIT_EX_DATE = datetime(2026, 3, 1, tzinfo=timezone.utc)
_AFTER = datetime(2026, 4, 1, tzinfo=timezone.utc)

_TWO_FOR_ONE = StockSplit(ex_date=_SPLIT_EX_DATE, ratio=Decimal(2))


# --------------------------------------------------------------------------
# split_factor
# --------------------------------------------------------------------------


def test_split_factor_bar_before_ex_date_within_as_of_applies_ratio() -> None:
    factor = split_factor([_TWO_FOR_ONE], bar_time=_BEFORE, as_of=_AFTER)
    assert factor == Decimal(2)


def test_split_factor_bar_after_ex_date_is_unaffected() -> None:
    factor = split_factor([_TWO_FOR_ONE], bar_time=_SPLIT_EX_DATE, as_of=_AFTER)
    assert factor == Decimal(1)


def test_split_factor_as_of_before_ex_date_is_unaffected() -> None:
    """as_of가 낙일(ex_date) 이전이면 그 분할은 아직 반영 대상이 아니다."""
    factor = split_factor(
        [_TWO_FOR_ONE], bar_time=_BEFORE, as_of=datetime(2026, 2, 15, tzinfo=timezone.utc)
    )
    assert factor == Decimal(1)


def test_split_factor_multiple_splits_compound() -> None:
    second = StockSplit(ex_date=datetime(2026, 5, 1, tzinfo=timezone.utc), ratio=Decimal(3))
    factor = split_factor(
        [_TWO_FOR_ONE, second], bar_time=_BEFORE, as_of=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    assert factor == Decimal(6)


def test_split_factor_rejects_reversed_window() -> None:
    with pytest.raises(ValueError, match="as_of"):
        split_factor([_TWO_FOR_ONE], bar_time=_AFTER, as_of=_BEFORE)


def test_split_factor_rejects_naive_bar_time() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        split_factor([_TWO_FOR_ONE], bar_time=datetime(2026, 2, 1), as_of=_AFTER)


def test_split_factor_rejects_non_positive_ratio() -> None:
    bad = StockSplit(ex_date=_SPLIT_EX_DATE, ratio=Decimal(0))
    with pytest.raises(ValueError, match="ratio"):
        split_factor([bad], bar_time=_BEFORE, as_of=_AFTER)


# --------------------------------------------------------------------------
# dividend_factor
# --------------------------------------------------------------------------


def test_dividend_factor_applies_crsp_style_reduction() -> None:
    dividend = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(2), prior_close=Decimal(100))
    factor = dividend_factor([dividend], bar_time=_BEFORE, as_of=_AFTER)
    assert factor == Decimal("0.98")


def test_dividend_factor_multiple_dividends_compound() -> None:
    first = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(2), prior_close=Decimal(100))
    second = CashDividend(
        ex_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        amount=Decimal(1),
        prior_close=Decimal(50),
    )
    factor = dividend_factor(
        [first, second], bar_time=_BEFORE, as_of=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    assert factor == Decimal("0.98") * Decimal("0.98")


def test_dividend_factor_rejects_negative_amount() -> None:
    bad = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(-1), prior_close=Decimal(100))
    with pytest.raises(ValueError, match="amount"):
        dividend_factor([bad], bar_time=_BEFORE, as_of=_AFTER)


def test_dividend_factor_rejects_non_positive_prior_close() -> None:
    bad = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(1), prior_close=Decimal(0))
    with pytest.raises(ValueError, match="prior_close"):
        dividend_factor([bad], bar_time=_BEFORE, as_of=_AFTER)


# --------------------------------------------------------------------------
# adjust_price_for_splits / adjust_quantity_for_splits / adjust_price_for_dividends
# --------------------------------------------------------------------------


def test_adjust_price_for_splits_halves_price_across_two_for_one_split() -> None:
    result = adjust_price_for_splits(
        _ADJ_ON, [_TWO_FOR_ONE], raw_price=Decimal(100), bar_time=_BEFORE, as_of=_AFTER
    )
    assert result.raw == Decimal(100)
    assert result.adjusted == Decimal(50)
    assert result.applied is True


def test_adjust_quantity_for_splits_doubles_quantity_across_two_for_one_split() -> None:
    result = adjust_quantity_for_splits(
        _ADJ_ON, [_TWO_FOR_ONE], raw_quantity=Decimal(10), bar_time=_BEFORE, as_of=_AFTER
    )
    assert result.raw == Decimal(10)
    assert result.adjusted == Decimal(20)
    assert result.applied is True


def test_adjust_price_for_splits_disabled_passes_raw_through_but_flags_unapplied() -> None:
    """조정을 껐을 때 raw==adjusted가 되더라도 `applied=False`가 함께
    돌아와 "미조정 모드"임을 결과 지표가 구분할 수 있어야 한다(조용히
    통과 금지, BT-20 DoD)."""

    result = adjust_price_for_splits(
        AdjustmentsConfig(splits=False, dividends=True),
        [_TWO_FOR_ONE],
        raw_price=Decimal(100),
        bar_time=_BEFORE,
        as_of=_AFTER,
    )
    assert result.adjusted == Decimal(100)
    assert result.applied is False


def test_adjust_price_for_dividends_disabled_passes_raw_through_but_flags_unapplied() -> None:
    dividend = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(2), prior_close=Decimal(100))
    result = adjust_price_for_dividends(
        AdjustmentsConfig(splits=True, dividends=False),
        [dividend],
        raw_price=Decimal(100),
        bar_time=_BEFORE,
        as_of=_AFTER,
    )
    assert result.adjusted == Decimal(100)
    assert result.applied is False


def test_adjust_price_for_splits_rejects_negative_raw_price() -> None:
    with pytest.raises(ValueError, match="raw_price"):
        adjust_price_for_splits(
            _ADJ_ON, [_TWO_FOR_ONE], raw_price=Decimal(-1), bar_time=_BEFORE, as_of=_AFTER
        )


def test_adjust_quantity_for_splits_rejects_negative_raw_quantity() -> None:
    with pytest.raises(ValueError, match="raw_quantity"):
        adjust_quantity_for_splits(
            _ADJ_ON, [_TWO_FOR_ONE], raw_quantity=Decimal(-1), bar_time=_BEFORE, as_of=_AFTER
        )


# --------------------------------------------------------------------------
# adjust_fill — DoD: 2:1 분할 픽스처에서 조정 체결과 원시 체결이 알려진
# 비율만큼 차이 + 대금(price*quantity) 불변
# --------------------------------------------------------------------------


def test_adjust_fill_two_for_one_split_scales_price_and_quantity_by_known_ratio() -> None:
    result = adjust_fill(
        _ADJ_ON,
        raw_price=Decimal(100),
        raw_quantity=Decimal(10),
        bar_time=_BEFORE,
        as_of=_AFTER,
        splits=[_TWO_FOR_ONE],
    )
    assert isinstance(result, AdjustedFill)
    assert result.adjusted_price == result.raw_price / Decimal(2)
    assert result.adjusted_quantity == result.raw_quantity * Decimal(2)
    assert result.splits_applied is True
    # 대금(price*quantity)은 분할 전후로 불변이어야 한다.
    raw_notional = result.raw_price * result.raw_quantity
    adjusted_notional = result.adjusted_price * result.adjusted_quantity
    assert raw_notional == adjusted_notional


def test_adjust_fill_with_dividend_reduces_adjusted_price_further() -> None:
    dividend = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(2), prior_close=Decimal(100))
    result = adjust_fill(
        _ADJ_ON,
        raw_price=Decimal(100),
        raw_quantity=Decimal(10),
        bar_time=_BEFORE,
        as_of=_AFTER,
        splits=[_TWO_FOR_ONE],
        dividends=[dividend],
    )
    # 분할로 50 → 배당 조정(0.98배)으로 49.
    assert result.adjusted_price == Decimal("49")
    assert result.splits_applied is True
    assert result.dividends_applied is True


def test_adjust_fill_unadjusted_mode_flags_both_dimensions_unapplied() -> None:
    """DoD "미조정 모드는 결과 지표에 플래그로 표시" — splits/dividends를
    모두 끄면 원시값 그대로 나오되 두 플래그 모두 False로 남아야 한다."""

    dividend = CashDividend(ex_date=_SPLIT_EX_DATE, amount=Decimal(2), prior_close=Decimal(100))
    result = adjust_fill(
        _ADJ_OFF,
        raw_price=Decimal(100),
        raw_quantity=Decimal(10),
        bar_time=_BEFORE,
        as_of=_AFTER,
        splits=[_TWO_FOR_ONE],
        dividends=[dividend],
    )
    assert result.adjusted_price == Decimal(100)
    assert result.adjusted_quantity == Decimal(10)
    assert result.splits_applied is False
    assert result.dividends_applied is False


def test_adjust_fill_no_corporate_actions_is_identity_and_still_flagged_applied() -> None:
    """조정은 켜져 있지만 해당 종목에 이벤트가 없으면 배수가 1이라 값은
    raw와 같지만, "조정 모드가 켜져 있었다"는 사실은 `applied=True`로
    남아 "조정을 깜빡한 것"과 구분된다."""

    result = adjust_fill(
        _ADJ_ON,
        raw_price=Decimal(100),
        raw_quantity=Decimal(10),
        bar_time=_BEFORE,
        as_of=_AFTER,
    )
    assert result.adjusted_price == Decimal(100)
    assert result.adjusted_quantity == Decimal(10)
    assert result.splits_applied is True
    assert result.dividends_applied is True
