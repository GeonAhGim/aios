"""LA-8 — market_data/domain/corporate_actions/adjustment.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8.

핵심 케이스(§9.2 LA-8): 같은 종목에 분할이 연속 2회 이상 있어도 각 캔들은
자기 날짜보다 뒤에 일어난 조정만 누적 반영해야 한다. ratio<=0은 예외.

DEEPEN(task-2954, docs/audit/DEPTH_LA_LB_LC.md original task-411): this module
is a pure function with no I/O, so the DEPTH audit's 4 failure-injection/
perf-assertion axes cannot be applied literally (see the same DEEPEN section
in `test_timeframe.py` -- pure domain-function leaves translate the spirit of
those axes instead). Added below: (1) one more negative case -- a batch that
mixes a healthy action for one instrument with a corrupted one for another
still fails closed as a whole, (2) failure injection -- since there is no
adapter to inject a fault into, this simulates a corrupted `action_type` that
bypassed Literal validation via `model_construct` (e.g. a stale value left
over from a looser prior schema), (3) large-batch timing is observed but not
asserted on (same policy as `test_lineage.py`'s
`test_batch_hash_large_batch_stays_order_independent` -- avoids permanent
CI-red, precedent 3ea1fc1/9bdcd21).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
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
    InvalidActionTypeError,
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


def test_factor_chain_fails_closed_when_one_action_among_many_is_invalid() -> None:
    """negative (DEEPEN task-2954): a healthy adjustment for one instrument
    mixed into the same batch as a corrupted one for another instrument must
    not let the healthy instrument compute quietly -- the whole batch must
    fail closed (no partial success)."""
    good = _split(uuid4(), date(2024, 1, 10), "2")
    bad = _split(uuid4(), date(2024, 3, 1), "0")

    with pytest.raises(InvalidRatioError):
        factor_chain([good, bad], datetime(2024, 12, 1, tzinfo=timezone.utc))


def test_factor_chain_fails_closed_on_corrupted_action_type() -> None:
    """failure injection (DEEPEN task-2954): this module has no I/O, so there
    is no adapter to inject a fault into. The one realistic fault shape is a
    corrupted `action_type` that bypassed Literal validation (e.g. a stale
    value left over from a looser prior schema), simulated here via
    `model_construct` (skips pydantic validation) -- it must die immediately
    instead of being silently treated as a SPLIT."""
    action = CorporateAction.model_construct(
        action_type="SPINOFF",
        instrument_id=uuid4(),
        ex_date=date(2024, 1, 1),
        ratio=Decimal("2"),
        cash_amount=None,
        source_ref="test",
        known_at=None,
        schema_version="v1",
    )

    with pytest.raises(InvalidActionTypeError):
        factor_chain([action], datetime(2024, 12, 1, tzinfo=timezone.utc))


def test_factor_chain_and_adjust_large_batch_stays_correct() -> None:
    """Observes large-batch timing (print) without asserting a numeric bound
    -- same policy as `test_lineage.py`'s
    `test_batch_hash_large_batch_stays_order_independent` (avoids permanent
    CI-red, precedent 3ea1fc1/9bdcd21). This leaf's invariant (each candle
    only reflects adjustments after its own date) must still hold at scale."""
    instrument_id = uuid4()
    base_date = date(2020, 1, 1)
    # Only 40 splits go on the instrument under assertion -- 2^40 is the
    # largest exponent at which sequential multiplication and a single
    # division stay byte-identical under the default Decimal precision (28
    # significant digits, measured above), so the large batch can still be
    # checked exactly. The rest are spread across other instruments just to
    # fill out the batch size (500) -- more realistic anyway, since real
    # batches mix adjustments for many instruments.
    actions = [_split(instrument_id, base_date + timedelta(days=i), "2") for i in range(40)]
    actions += [_split(uuid4(), base_date + timedelta(days=i), "2") for i in range(460)]
    as_of = datetime(2021, 6, 1, tzinfo=timezone.utc)
    candles = [
        _candle(
            instrument_id,
            datetime(2019, 12, 31, tzinfo=timezone.utc) + timedelta(days=i),
            Decimal("100"),
        )
        for i in range(2_000)
    ]

    started = time.perf_counter()
    factors = factor_chain(actions, as_of)
    adjusted = adjust(candles, factors)
    elapsed = time.perf_counter() - started
    print(f"\nfactor_chain+adjust latency (500 splits, 2000 candles): {elapsed:.4f}s")

    # The earliest candle reflects all 40 splits (2:1 each): divided by 2^40.
    assert adjusted[0].close == Decimal("100") / (Decimal(2) ** 40)
    # An adjustment past `as_of` is not reflected.
    assert adjusted[-1].close == Decimal("100")
