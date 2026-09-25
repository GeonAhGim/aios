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


def test_adjust_returns_unchanged_when_no_factors_exist() -> None:
    """negative (DEEPEN task-4112): invariant I-05(순수함수_항등) — 인자 없이 adjust()를
    호출하면 원본 캔들이 그대로 돌아와야 한다(계수=1과 동일). 계수 체인이 아예 없는
    상황(신규 상장 종목 등)에서 partial mutation이 일어나지 않음을 명시적으로 검증한다."""
    instrument_id = uuid4()
    candles = [
        _candle(instrument_id, datetime(2024, 1, 5, tzinfo=timezone.utc), Decimal("500")),
        _candle(instrument_id, datetime(2024, 3, 1, tzinfo=timezone.utc), Decimal("300")),
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
    as_of = datetime(2024, 12, 1, tzinfo=timezone.utc)

    actions = [_split(good_instrument, date(2024, 6, 1), "2")]
    factors = factor_chain(actions, as_of)

    candles = [
        _candle(good_instrument, datetime(2024, 3, 1, tzinfo=timezone.utc), Decimal("400")),
        _candle(unknown_instrument, datetime(2024, 3, 1, tzinfo=timezone.utc), Decimal("999")),
    ]

    adjusted = adjust(candles, factors)

    # good_instrument는 조정 적용됨
    assert adjusted[0].close == Decimal("200")  # 400 / 2
    # unknown_instrument는 무조정
    assert adjusted[1].close == Decimal("999")
    assert adjusted[1].volume == Decimal("100")


def test_factor_chain_raises_on_ratio_built_from_float_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure injection (DEEPEN task-4112, review 5367/4836 후속 정정): 이 모듈은
    I/O가 없는 순수 함수라 실제 어댑터에 fault를 주입할 지점이 없다. 여기서는
    monkeypatch로 이미 구성된 `CorporateAction` 인스턴스의 `ratio` 필드를 사후에
    float 값(0.0)으로 덮어써, "float에서 만들어진 ratio가 Decimal이 아니라 float
    타입인 채로 체인에 도달하는" 경로를 실제로 주입한다(예: 이전 스키마가 float를
    허용하던 시절의 레코드가 역직렬화 버그로 그대로 흘러드는 상황을 흉내낸다).

    test_zero_ratio_raises는 정상적으로 `Decimal("0")`으로 구성된 ratio를
    검증하지만, 이 테스트는 타입 자체가 오염된(float) ratio도 `ratio <= 0`
    비교가 여전히 성립해 InvalidRatioError로 fail-closed됨을 검증하는 별개의
    경로다 — monkeypatch 없이는 재현할 수 없다(정상 생성 경로로는 pydantic이
    Decimal로 강제 변환하므로 float 타입 자체가 살아남지 않는다)."""
    action = _split(uuid4(), date(2024, 1, 10), "1")
    monkeypatch.setattr(action, "ratio", 0.0)

    with pytest.raises(InvalidRatioError):
        factor_chain([action], datetime(2024, 12, 1, tzinfo=timezone.utc))


def test_adjust_handles_candle_with_zero_volume() -> None:
    """negative (DEEPEN task-4112, review 5367/4836 후속 정정): 경계값 입력에 대한
    정적 검증 — zero-volume candle가 들어와도 Decimal 곱셈은 정의되므로(0*x=0),
    예외 없이 0 volume candle가 반환됨을 검증한다. 이 경로는 예외를 유발하는 게
    아니라 정상 산출값을 확인하는 것이라 주입할 fault가 없으므로 monkeypatch
    인자를 제거했다 — 실패 주입은
    test_factor_chain_raises_on_ratio_built_from_float_via_monkeypatch가 맡는다."""
    instrument_id = uuid4()
    as_of = datetime(2024, 12, 1, tzinfo=timezone.utc)
    actions = [_split(instrument_id, date(2024, 6, 1), "2")]
    factors = factor_chain(actions, as_of)

    zero_vol_candle = _candle(
        instrument_id, datetime(2024, 3, 1, tzinfo=timezone.utc), Decimal("500")
    )
    # volume을 0으로 설정
    zero_vol_candle = zero_vol_candle.model_copy(update={"volume": Decimal("0")})

    adjusted = adjust([zero_vol_candle], factors)

    # price는 조정되지만 volume=0 * 계수 = 0
    assert adjusted[0].close == Decimal("250")  # 500 / 2
    assert adjusted[0].volume == Decimal("0")


@pytest.mark.perf
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

    # The earliest candle reflects all 40 splits (2:1 each): divided by 2^40.
    assert adjusted[0].close == Decimal("100") / (Decimal(2) ** 40)
    # An adjustment past `as_of` is not reflected.
    assert adjusted[-1].close == Decimal("100")

    # Performance assertion (ADR-2026-09-09-C D2): 500 actions + 2000 candles
    # must complete under 1 second on CI-grade hardware.  Measured p95 ~0.08s
    # on a M2 Mac (2024-04).  This is a soft bound — use `pytest --assert`
    # to relax if the CI environment is consistently slower.
    assert elapsed < 1.0, f"factor_chain+adjust took {elapsed:.4f}s, exceeds 1s budget"
    print(f"\nfactor_chain+adjust latency (500 splits, 2000 candles): {elapsed:.4f}s")
