"""LA-8 — market_data/domain/corporate_actions/adjustment.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8.

핵심 케이스(§9.2 LA-8): 같은 종목에 분할이 연속 2회 이상 있어도 각 캔들은
자기 날짜보다 뒤에 일어난 조정만 누적 반영해야 한다. ratio<=0은 예외.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    CorporateAction,
    SeriesKey,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.corporate_actions.adjustment import (
    InvalidRatioError,
    adjust,
    factor_chain,
)


def _candle(instrument_id: object, open_time: datetime, close: Decimal) -> CandleRecord:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=instrument_id, timeframe=Timeframe.D1)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("100"),
    )


def _split(instrument_id: object, ex_date: date, ratio: str) -> CorporateAction:
    return CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=ex_date,
        ratio=Decimal(ratio),
        source_ref="test",
    )


def test_two_consecutive_splits_accumulate_price_factor() -> None:
    # 2:1 다음 4:1 — 둘 다 유한소수로 나누어떨어져 Decimal 반올림 오차 없이
    # "정확"을 정밀하게 검증할 수 있다(1/6처럼 순환소수인 비율은 Decimal
    # context precision에서 반올림되므로 이 테스트의 목적에 맞지 않는다).
    instrument_id = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=timezone.utc)
    actions = [
        _split(instrument_id, date(2024, 1, 10), "2"),
        _split(instrument_id, date(2024, 6, 10), "4"),
    ]

    factors = factor_chain(actions, as_of)

    assert [f.effective_date for f in factors] == [date(2024, 1, 10), date(2024, 6, 10)]
    before_both = next(f for f in factors if f.effective_date == date(2024, 1, 10))
    assert before_both.price_factor == Decimal(1) / Decimal(8)
    assert before_both.volume_factor == Decimal(8)
    between = next(f for f in factors if f.effective_date == date(2024, 6, 10))
    assert between.price_factor == Decimal(1) / Decimal(4)
    assert between.volume_factor == Decimal(4)


def test_adjust_applies_cumulative_factor_only_for_bars_before_each_split() -> None:
    instrument_id = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=timezone.utc)
    actions = [
        _split(instrument_id, date(2024, 1, 10), "2"),
        _split(instrument_id, date(2024, 6, 10), "4"),
    ]
    factors = factor_chain(actions, as_of)
    candles = [
        _candle(instrument_id, datetime(2024, 1, 5, tzinfo=timezone.utc), Decimal("600")),
        _candle(instrument_id, datetime(2024, 3, 1, tzinfo=timezone.utc), Decimal("300")),
        _candle(instrument_id, datetime(2024, 7, 1, tzinfo=timezone.utc), Decimal("100")),
    ]

    adjusted = adjust(candles, factors)

    assert adjusted[0].close == Decimal("75")  # 600 / (2*4)
    assert adjusted[0].volume == Decimal("800")  # 100 * 8
    assert adjusted[1].close == Decimal("75")  # 300 / 4, 2024-01 split already reflected in raw
    assert adjusted[2].close == Decimal("100")  # 이후 캔들은 무조정


def test_factor_chain_ignores_actions_after_as_of() -> None:
    instrument_id = uuid4()
    as_of = datetime(2024, 3, 1, tzinfo=timezone.utc)
    actions = [
        _split(instrument_id, date(2024, 1, 10), "2"),
        _split(instrument_id, date(2024, 6, 10), "3"),
    ]

    factors = factor_chain(actions, as_of)

    assert len(factors) == 1
    assert factors[0].effective_date == date(2024, 1, 10)
    assert factors[0].price_factor == Decimal(1) / Decimal(2)


def test_reverse_split_scales_price_up_and_volume_down() -> None:
    instrument_id = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=timezone.utc)
    action = CorporateAction(
        action_type="REVERSE_SPLIT",
        instrument_id=instrument_id,
        ex_date=date(2024, 6, 1),
        ratio=Decimal("2"),
        source_ref="test",
    )

    factors = factor_chain([action], as_of)

    assert factors[0].price_factor == Decimal("2")
    assert factors[0].volume_factor == Decimal(1) / Decimal(2)


def test_zero_ratio_raises() -> None:
    action = _split(uuid4(), date(2024, 1, 1), "0")
    with pytest.raises(InvalidRatioError):
        factor_chain([action], datetime(2024, 12, 1, tzinfo=timezone.utc))


def test_negative_ratio_raises() -> None:
    action = _split(uuid4(), date(2024, 1, 1), "-1")
    with pytest.raises(InvalidRatioError):
        factor_chain([action], datetime(2024, 12, 1, tzinfo=timezone.utc))
