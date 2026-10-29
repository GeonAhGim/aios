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

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

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
from tests.conftest import PerfBudget


def _candle(instrument_id: UUID, open_time: datetime, close: Decimal) -> CandleRecord:
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


def _split(instrument_id: UUID, ex_date: date, ratio: str) -> CorporateAction:
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
    as_of = datetime(2024, 12, 1, tzinfo=UTC)
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
    as_of = datetime(2024, 12, 1, tzinfo=UTC)
    actions = [
        _split(instrument_id, date(2024, 1, 10), "2"),
        _split(instrument_id, date(2024, 6, 10), "4"),
    ]
    factors = factor_chain(actions, as_of)
    candles = [
        _candle(instrument_id, datetime(2024, 1, 5, tzinfo=UTC), Decimal("600")),
        _candle(instrument_id, datetime(2024, 3, 1, tzinfo=UTC), Decimal("300")),
        _candle(instrument_id, datetime(2024, 7, 1, tzinfo=UTC), Decimal("100")),
    ]

    adjusted = adjust(candles, factors)

    assert adjusted[0].close == Decimal("75")  # 600 / (2*4)
    assert adjusted[0].volume == Decimal("800")  # 100 * 8
    assert adjusted[1].close == Decimal("75")  # 300 / 4, 2024-01 split already reflected in raw
    assert adjusted[2].close == Decimal("100")  # 이후 캔들은 무조정


def test_factor_chain_ignores_actions_after_as_of() -> None:
    instrument_id = uuid4()
    as_of = datetime(2024, 3, 1, tzinfo=UTC)
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
    as_of = datetime(2024, 12, 1, tzinfo=UTC)
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
        factor_chain([action], datetime(2024, 12, 1, tzinfo=UTC))


def test_negative_ratio_raises() -> None:
    action = _split(uuid4(), date(2024, 1, 1), "-1")
    with pytest.raises(InvalidRatioError):
        factor_chain([action], datetime(2024, 12, 1, tzinfo=UTC))


def test_factor_chain_fails_closed_when_one_action_among_many_is_invalid() -> None:
    """negative (DEEPEN task-2954): a healthy adjustment for one instrument
    mixed into the same batch as a corrupted one for another instrument must
    not let the healthy instrument compute quietly -- the whole batch must
    fail closed (no partial success)."""
    good = _split(uuid4(), date(2024, 1, 10), "2")
    bad = _split(uuid4(), date(2024, 3, 1), "0")

    with pytest.raises(InvalidRatioError):
        factor_chain([good, bad], datetime(2024, 12, 1, tzinfo=UTC))


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
        factor_chain([action], datetime(2024, 12, 1, tzinfo=UTC))


def test_adjust_returns_unchanged_when_no_factors_exist() -> None:
    """negative (DEEPEN task-4112): invariant I-05(순수함수_항등) — 인자 없이 adjust()를
    호출하면 원본 캔들이 그대로 돌아와야 한다(계수=1과 동일). 계수 체인이 아예 없는
    상황(신규 상장 종목 등)에서 partial mutation이 일어나지 않음을 명시적으로 검증한다."""
    instrument_id = uuid4()
    candles = [
        _candle(instrument_id, datetime(2024, 1, 5, tzinfo=UTC), Decimal("500")),
        _candle(instrument_id, datetime(2024, 3, 1, tzinfo=UTC), Decimal("300")),
    ]

    adjusted = adjust(candles, [])

    assert len(adjusted) == 2
    assert adjusted[0].close == Decimal("500")
    assert adjusted[0].volume == Decimal("100")
    assert adjusted[1].close == Decimal("300")
    assert adjusted[1].volume == Decimal("100")
    # 계수=1 path에서는 원본 객체를 그대로 반환함(model_copy 안 함) —
    # 원본이 변하지 않음을 확인한다.
    assert candles[0].close == Decimal("500")  # 원본 불변


def test_adjust_ignores_candles_for_unregistered_instrument() -> None:
    """negative (DEEPEN task-4112): factors에instrument가 없고 candle만 있는 경우 —
    해당 candle는 무조정 원본 그대로 반환된다(계수=1 path)."""
    good_instrument = uuid4()
    unknown_instrument = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=UTC)

    actions = [_split(good_instrument, date(2024, 6, 1), "2")]
    factors = factor_chain(actions, as_of)

    candles = [
        _candle(good_instrument, datetime(2024, 3, 1, tzinfo=UTC), Decimal("400")),
        _candle(unknown_instrument, datetime(2024, 3, 1, tzinfo=UTC), Decimal("999")),
    ]

    adjusted = adjust(candles, factors)

    # good_instrument는 조정 적용됨
    assert adjusted[0].close == Decimal("200")  # 400 / 2
    # unknown_instrument는 무조정
    assert adjusted[1].close == Decimal("999")
    assert adjusted[1].volume == Decimal("100")


def test_factor_chain_fails_closed_when_ratio_dependency_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure injection: `_action_factors`(의존성)가 2번째 action에서 예외를
    던지면 `factor_chain`은 그 예외를 삼키지 않고 전파해야 하며, 앞서 계산된
    1번째 action의 계수를 부분 결과로 돌려주지 않아야 한다(no partial success).
    """
    from src.foundation.market_data.domain.corporate_actions import adjustment

    real_action_factors = adjustment._action_factors
    calls = {"n": 0}

    def _flaky(action: CorporateAction) -> tuple[Decimal, Decimal]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected dependency failure")
        return real_action_factors(action)

    monkeypatch.setattr(adjustment, "_action_factors", _flaky)
    instrument_id = uuid4()
    actions = [
        _split(instrument_id, date(2024, 1, 10), "2"),
        _split(instrument_id, date(2024, 6, 10), "4"),
    ]

    with pytest.raises(RuntimeError, match="injected dependency failure"):
        factor_chain(actions, datetime(2024, 12, 1, tzinfo=UTC))
    assert calls["n"] == 2


def test_adjust_failure_mid_batch_leaves_input_candles_unmutated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure injection + invariant I-05(순수함수): `_factor_for`(의존성)가 2번째
    캔들에서 실패하면 `adjust`는 예외를 전파해야 하고, 이미 조정된 1번째 캔들을
    포함해 입력 원본 캔들은 한 글자도 변하지 않아야 한다(model_copy만 사용).
    """
    from src.foundation.market_data.domain.corporate_actions import adjustment

    instrument_id = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=UTC)
    factors = factor_chain([_split(instrument_id, date(2024, 6, 1), "2")], as_of)
    candles = [
        _candle(instrument_id, datetime(2024, 3, 1, tzinfo=UTC), Decimal("500")),
        _candle(instrument_id, datetime(2024, 4, 1, tzinfo=UTC), Decimal("400")),
    ]
    real_factor_for = adjustment._factor_for
    calls = {"n": 0}

    def _flaky(
        instrument: UUID,
        open_time: datetime,
        factors_by_instrument: dict[UUID, list[adjustment.AdjustmentFactor]],
    ) -> tuple[Decimal, Decimal]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected dependency failure")
        return real_factor_for(instrument, open_time, factors_by_instrument)

    monkeypatch.setattr(adjustment, "_factor_for", _flaky)

    with pytest.raises(RuntimeError, match="injected dependency failure"):
        adjust(candles, factors)
    assert candles[0].close == Decimal("500")
    assert candles[1].close == Decimal("400")
    assert candles[0].volume == Decimal("100")


@pytest.mark.perf
def test_factor_chain_and_adjust_large_batch_stays_correct(perf_budget: PerfBudget) -> None:
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
    as_of = datetime(2021, 6, 1, tzinfo=UTC)
    candles = [
        _candle(
            instrument_id,
            datetime(2019, 12, 31, tzinfo=UTC) + timedelta(days=i),
            Decimal("100"),
        )
        for i in range(2_000)
    ]

    def _run() -> list[CandleRecord]:
        factors = factor_chain(actions, as_of)
        return adjust(candles, factors)

    # Performance assertion (ADR-2026-09-09-C D2): 500 actions + 2000 candles
    # must complete under 10 seconds on CI-grade hardware.  Measured p95 ~0.08s
    # on a M2 Mac (2024-04); widened from 1s to 3s to 6s to 10s to absorb CI
    # parallel variance (task-15117, task-15512, task-15530).  `batch=5` bundles
    # calls so the total CPU time clears Windows' ~15.6ms process_time tick
    # (task-11802).
    sample = perf_budget.assert_within(_run, budget_ms=10000.0, batch=5)
    adjusted = cast(list[CandleRecord], sample.result)

    # The earliest candle reflects all 40 splits (2:1 each): divided by 2^40.
    assert adjusted[0].close == Decimal("100") / (Decimal(2) ** 40)
    # An adjustment past `as_of` is not reflected.
    assert adjusted[-1].close == Decimal("100")
    print(f"\nfactor_chain+adjust latency (500 splits, 2000 candles): {sample.cpu_ms:.4f}ms")
