"""BT-7 magnifier — 결정론·확대 규칙·미래 참조 금지·TF 정합성.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-7, §9.5 BT-7 DoD("하위 TF 체결 순서 결정론").

모든 기대 순서는 손으로 계산해 `Decimal` exact 비교로 단언한다.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.domain.magnifier import (
    HigherBar,
    IncompatibleMagnifierTimeframeError,
    LookAheadError,
    magnify,
    validate_magnifier_config,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

_UP_BAR = HigherBar(
    open_time=_T0,
    open=Decimal("100"),
    high=Decimal("110"),
    low=Decimal("95"),
    close=Decimal("105"),
)
_DOWN_BAR = HigherBar(
    open_time=_T0,
    open=Decimal("100"),
    high=Decimal("105"),
    low=Decimal("90"),
    close=Decimal("95"),
)


def _empty_columns() -> CandleColumns:
    return CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])


def _m1_columns(rows: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal]]) -> CandleColumns:
    return CandleColumns(
        ts=[r[0] for r in rows],
        open=[r[1] for r in rows],
        high=[r[2] for r in rows],
        low=[r[3] for r in rows],
        close=[r[4] for r in rows],
        volume=[Decimal("0") for _ in rows],
        quote_volume=[None for _ in rows],
    )


# --------------------------------------------------------------------------
# 확대 규칙(봉 단위, 하위 봉 없음) — 상승봉/하락봉 각각 손 계산 exact 비교
# --------------------------------------------------------------------------


def test_magnify_up_bar_no_lower_bars_visits_low_before_high() -> None:
    """양봉(close>=open) — open → low → high → close."""
    order = magnify(_UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=None)
    assert order == (Decimal("100"), Decimal("95"), Decimal("110"), Decimal("105"))


def test_magnify_down_bar_no_lower_bars_visits_high_before_low() -> None:
    """음봉(close<open) — open → high → low → close."""
    order = magnify(_DOWN_BAR, higher_tf=Timeframe.M5, magnifier_tf=None)
    assert order == (Decimal("100"), Decimal("105"), Decimal("90"), Decimal("95"))


def test_magnify_doji_close_equals_open_treated_as_up_bar() -> None:
    bar = HigherBar(
        open_time=_T0, open=Decimal("100"), high=Decimal("108"), low=Decimal("92"),
        close=Decimal("100"),
    )
    order = magnify(bar, higher_tf=Timeframe.M5, magnifier_tf=None)
    assert order == (Decimal("100"), Decimal("92"), Decimal("108"), Decimal("100"))


def test_magnify_none_magnifier_tf_ignores_provided_lower_bars() -> None:
    """magnifier_tf=None이면 lower_bars가 있어도 무시하고 봉 단위로 되돌아간다
    (예외 아님, §DoD (4))."""
    lower = _m1_columns(
        [(_T0, Decimal("999"), Decimal("999"), Decimal("999"), Decimal("999"))]
    )
    order = magnify(_UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=None, lower_bars=lower)
    assert order == (Decimal("100"), Decimal("95"), Decimal("110"), Decimal("105"))


def test_magnify_empty_lower_bars_falls_back_to_higher_bar_expansion() -> None:
    """하위 TF 데이터가 실제로 갭이면(빈 컬럼) 상위 봉을 확대한다."""
    order = magnify(
        _UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=Timeframe.M1, lower_bars=_empty_columns()
    )
    assert order == (Decimal("100"), Decimal("95"), Decimal("110"), Decimal("105"))


# --------------------------------------------------------------------------
# 실제 하위 TF 봉이 있으면 그것을 쓴다 — 봉마다 확대해 시간순으로 이어붙인다
# --------------------------------------------------------------------------


def test_magnify_uses_real_lower_bars_when_present_concatenated_in_order() -> None:
    rows = [
        (_T0 + timedelta(minutes=0), Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101")),
        (_T0 + timedelta(minutes=1), Decimal("101"), Decimal("101"), Decimal("98"), Decimal("99")),
        (_T0 + timedelta(minutes=2), Decimal("99"), Decimal("105"), Decimal("99"), Decimal("104")),
        (
            _T0 + timedelta(minutes=3),
            Decimal("104"),
            Decimal("106"),
            Decimal("103"),
            Decimal("103"),
        ),
        (
            _T0 + timedelta(minutes=4),
            Decimal("103"),
            Decimal("110"),
            Decimal("102"),
            Decimal("108"),
        ),
    ]
    order = magnify(
        _UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=Timeframe.M1, lower_bars=_m1_columns(rows)
    )
    assert order == (
        Decimal("100"), Decimal("99"), Decimal("102"), Decimal("101"),   # up
        Decimal("101"), Decimal("101"), Decimal("98"), Decimal("99"),    # down
        Decimal("99"), Decimal("99"), Decimal("105"), Decimal("104"),    # up
        Decimal("104"), Decimal("106"), Decimal("103"), Decimal("103"),  # down
        Decimal("103"), Decimal("102"), Decimal("110"), Decimal("108"),  # up
    )


def test_magnify_is_deterministic_across_repeated_calls() -> None:
    rows = [
        (_T0 + timedelta(minutes=0), Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101")),
        (
            _T0 + timedelta(minutes=1),
            Decimal("101"),
            Decimal("103"),
            Decimal("100"),
            Decimal("102"),
        ),
    ]
    columns = _m1_columns(rows)
    first = magnify(_UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=Timeframe.M1, lower_bars=columns)
    for _ in range(20):
        again = magnify(
            _UP_BAR, higher_tf=Timeframe.M5, magnifier_tf=Timeframe.M1, lower_bars=columns
        )
        assert again == first


# --------------------------------------------------------------------------
# 미래 참조 금지 — 상위 봉 시간창 밖의 하위 봉 접근은 예외
# --------------------------------------------------------------------------


def test_magnify_rejects_lower_bar_at_or_after_window_end_look_ahead() -> None:
    """window_end(=T0+5min)에 걸리는 하위 봉은 다음 상위 봉에 속한 "미래"
    데이터다 — 접근 자체를 거부한다."""
    rows = [
        (_T0, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101")),
        (
            _T0 + timedelta(minutes=5),
            Decimal("101"),
            Decimal("103"),
            Decimal("100"),
            Decimal("102"),
        ),
    ]
    with pytest.raises(LookAheadError):
        magnify(
            _UP_BAR,
            higher_tf=Timeframe.M5,
            magnifier_tf=Timeframe.M1,
            lower_bars=_m1_columns(rows),
        )


def test_magnify_rejects_lower_bar_before_window_start() -> None:
    rows = [
        (
            _T0 - timedelta(minutes=1),
            Decimal("100"),
            Decimal("102"),
            Decimal("99"),
            Decimal("101"),
        )
    ]
    with pytest.raises(LookAheadError):
        magnify(
            _UP_BAR,
            higher_tf=Timeframe.M5,
            magnifier_tf=Timeframe.M1,
            lower_bars=_m1_columns(rows),
        )


# --------------------------------------------------------------------------
# 상위/하위 TF 정합성 negative
# --------------------------------------------------------------------------


def test_validate_magnifier_config_accepts_none() -> None:
    validate_magnifier_config(higher_tf=Timeframe.M5, magnifier_tf=None)  # must not raise


def test_validate_magnifier_config_accepts_valid_divisor() -> None:
    validate_magnifier_config(higher_tf=Timeframe.H1, magnifier_tf=Timeframe.M15)  # must not raise


def test_validate_magnifier_config_rejects_equal_timeframe() -> None:
    with pytest.raises(IncompatibleMagnifierTimeframeError):
        validate_magnifier_config(higher_tf=Timeframe.M15, magnifier_tf=Timeframe.M15)


def test_validate_magnifier_config_rejects_magnifier_larger_than_higher() -> None:
    with pytest.raises(IncompatibleMagnifierTimeframeError):
        validate_magnifier_config(higher_tf=Timeframe.M5, magnifier_tf=Timeframe.M15)


def test_validate_magnifier_config_rejects_non_integer_multiple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 `Timeframe` 7종은 1/5/15/30/60/240/1440분의 약수 사슬이라 서로
    나눠떨어지지 않는 조합이 존재하지 않는다 — 그래도 방어 코드는 있어야
    하므로 `duration`을 몽키패치해 그 분기를 직접 검증한다."""

    def _fake_duration(tf: Timeframe) -> timedelta:
        return {Timeframe.H1: timedelta(minutes=70), Timeframe.M15: timedelta(minutes=15)}[tf]

    monkeypatch.setattr("src.foundation.backtest.domain.magnifier.duration", _fake_duration)
    with pytest.raises(IncompatibleMagnifierTimeframeError):
        validate_magnifier_config(higher_tf=Timeframe.H1, magnifier_tf=Timeframe.M15)


def test_magnify_rejects_incompatible_config_even_with_valid_lower_bars() -> None:
    """`magnify`는 자체적으로 `validate_magnifier_config`를 거친다 — 호출자가
    사전 검증을 빠뜨려도 fail-closed."""
    rows = [(_T0, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"))]
    with pytest.raises(IncompatibleMagnifierTimeframeError):
        magnify(
            _UP_BAR,
            higher_tf=Timeframe.M5,
            magnifier_tf=Timeframe.M15,
            lower_bars=_m1_columns(rows),
        )
