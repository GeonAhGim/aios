"""BT-2~6 체결 현실성 5모듈 — 정확값·경계·음수 거부(fail-closed).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-2~6, §3.4, §9.5 BT-2~6 DoD.

모든 기대값은 손으로 계산해 Decimal exact 비교로 단언한다(float 근사
비교 금지). 5개 소스 모듈이 서로 임포트하지 않으므로 이 테스트 파일도
모듈별 섹션을 독립적으로 구성한다.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.fill.commission import compute_commission
from src.foundation.backtest.domain.fill.latency import (
    delayed_arrival_time,
    resolve_execution_bar_index,
)
from src.foundation.backtest.domain.fill.order_types import (
    OcoResolution,
    OrderTypeDisabledError,
    TrailingStopState,
    ensure_order_type_enabled,
    is_limit_triggered,
    is_stop_triggered,
    resolve_oco,
    update_trailing_stop,
)
from src.foundation.backtest.domain.fill.partial_fill import compute_partial_fill
from src.foundation.backtest.domain.fill.slippage import apply_slippage
from src.foundation.backtest.domain.models_v2 import (
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    PercentSlippage,
    VenueTierCommission,
    VolumeImpactSlippage,
)

# --------------------------------------------------------------------------
# BT-2 slippage
# --------------------------------------------------------------------------


def test_fixed_slippage_buy_exact_value() -> None:
    model = FixedSlippage(bps=Decimal("10"))
    price = apply_slippage(
        model, side=OrderSide.BUY, reference_price=Decimal("100"), quantity=Decimal("1")
    )
    assert price == Decimal("100.100")


def test_fixed_slippage_sell_exact_value() -> None:
    model = FixedSlippage(bps=Decimal("10"))
    price = apply_slippage(
        model, side=OrderSide.SELL, reference_price=Decimal("100"), quantity=Decimal("1")
    )
    assert price == Decimal("99.900")


def test_percent_slippage_exact_value() -> None:
    model = PercentSlippage(pct=Decimal("0.02"))
    price = apply_slippage(
        model, side=OrderSide.BUY, reference_price=Decimal("50"), quantity=Decimal("1")
    )
    assert price == Decimal("51.00")


def test_volume_impact_slippage_exact_value_below_cap() -> None:
    model = VolumeImpactSlippage(k=Decimal("0.5"), participation_cap=Decimal("0.5"))
    price = apply_slippage(
        model,
        side=OrderSide.BUY,
        reference_price=Decimal("200"),
        quantity=Decimal("30"),
        bar_volume=Decimal("100"),
    )
    # participation = 30/100 = 0.3 (< cap 0.5); offset = 0.5*0.3 = 0.15
    assert price == Decimal("230.000")


def test_volume_impact_slippage_reaches_participation_cap() -> None:
    """주문량이 봉 거래량을 훨씬 초과해도 participation은 cap에서 멈춘다."""
    model = VolumeImpactSlippage(k=Decimal("0.5"), participation_cap=Decimal("0.5"))
    price = apply_slippage(
        model,
        side=OrderSide.BUY,
        reference_price=Decimal("200"),
        quantity=Decimal("1000"),
        bar_volume=Decimal("100"),
    )
    # participation = min(10, 0.5) = 0.5 (cap); offset = 0.5*0.5 = 0.25
    assert price == Decimal("250.00")


def test_volume_impact_slippage_requires_bar_volume() -> None:
    model = VolumeImpactSlippage(k=Decimal("0.5"), participation_cap=Decimal("0.5"))
    with pytest.raises(ValueError, match="bar_volume"):
        apply_slippage(
            model, side=OrderSide.BUY, reference_price=Decimal("200"), quantity=Decimal("1")
        )


def test_volume_impact_slippage_rejects_zero_bar_volume() -> None:
    model = VolumeImpactSlippage(k=Decimal("0.5"), participation_cap=Decimal("0.5"))
    with pytest.raises(ValueError, match="bar_volume"):
        apply_slippage(
            model,
            side=OrderSide.BUY,
            reference_price=Decimal("200"),
            quantity=Decimal("1"),
            bar_volume=Decimal("0"),
        )


def test_apply_slippage_rejects_negative_reference_price() -> None:
    model = FixedSlippage(bps=Decimal("1"))
    with pytest.raises(ValueError, match="reference_price"):
        apply_slippage(
            model, side=OrderSide.BUY, reference_price=Decimal("-1"), quantity=Decimal("1")
        )


def test_apply_slippage_rejects_negative_quantity() -> None:
    model = FixedSlippage(bps=Decimal("1"))
    with pytest.raises(ValueError, match="quantity"):
        apply_slippage(
            model, side=OrderSide.BUY, reference_price=Decimal("1"), quantity=Decimal("-1")
        )


def test_apply_slippage_rejects_nan_reference_price() -> None:
    model = FixedSlippage(bps=Decimal("1"))
    with pytest.raises(ValueError, match="reference_price"):
        apply_slippage(
            model, side=OrderSide.BUY, reference_price=Decimal("NaN"), quantity=Decimal("1")
        )


def test_fixed_slippage_model_rejects_negative_bps() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, contract-level
        FixedSlippage(bps=Decimal("-1"))


# --------------------------------------------------------------------------
# BT-3 commission
# --------------------------------------------------------------------------

_VENUE = VenueTierCommission(
    venue="BITGET", maker_bps=Decimal("2"), taker_bps=Decimal("4"), min_fee=Decimal("0.10")
)


def test_compute_commission_maker_exact_value() -> None:
    fee = compute_commission(_VENUE, is_maker=True, notional=Decimal("10000"))
    assert fee == Decimal("2.0000")


def test_compute_commission_taker_exact_value() -> None:
    fee = compute_commission(_VENUE, is_maker=False, notional=Decimal("10000"))
    assert fee == Decimal("4.0000")


def test_compute_commission_applies_min_fee_floor() -> None:
    """작은 체결에서는 비율 수수료가 min_fee보다 작아 min_fee가 적용된다."""
    fee = compute_commission(_VENUE, is_maker=True, notional=Decimal("10"))
    assert fee == Decimal("0.10")


def test_compute_commission_rejects_negative_notional() -> None:
    with pytest.raises(ValueError, match="notional"):
        compute_commission(_VENUE, is_maker=True, notional=Decimal("-1"))


def test_compute_commission_rejects_nan_notional() -> None:
    with pytest.raises(ValueError, match="notional"):
        compute_commission(_VENUE, is_maker=True, notional=Decimal("NaN"))


def test_venue_tier_commission_model_rejects_negative_min_fee() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, contract-level
        VenueTierCommission(
            venue="BITGET", maker_bps=Decimal("1"), taker_bps=Decimal("1"), min_fee=Decimal("-1")
        )


# --------------------------------------------------------------------------
# BT-4 latency
# --------------------------------------------------------------------------

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_delayed_arrival_time_exact_value() -> None:
    arrival = delayed_arrival_time(submitted_at=_T0, latency_ms=1500)
    assert arrival == _T0 + timedelta(milliseconds=1500)


def test_delayed_arrival_time_zero_latency_is_identity() -> None:
    arrival = delayed_arrival_time(submitted_at=_T0, latency_ms=0)
    assert arrival == _T0


def test_resolve_execution_bar_index_picks_first_bar_strictly_after_arrival() -> None:
    bars = [_T0, _T0 + timedelta(seconds=1), _T0 + timedelta(seconds=2)]
    index = resolve_execution_bar_index(submitted_at=_T0, latency_ms=500, bar_open_times=bars)
    assert index == 1


def test_resolve_execution_bar_index_zero_latency_still_excludes_same_instant_bar() -> None:
    """latency=0이어도 도달 시각과 정확히 같은 bar는 아직 그 정보를 못 쓴다
    (look-ahead 금지) — 그 다음 bar가 선택돼야 한다."""
    bars = [_T0, _T0 + timedelta(seconds=1)]
    index = resolve_execution_bar_index(submitted_at=_T0, latency_ms=0, bar_open_times=bars)
    assert index == 1


def test_resolve_execution_bar_index_raises_when_no_later_bar_exists() -> None:
    bars = [_T0]
    with pytest.raises(LookupError):
        resolve_execution_bar_index(submitted_at=_T0, latency_ms=0, bar_open_times=bars)


def test_delayed_arrival_time_rejects_negative_latency_ms() -> None:
    with pytest.raises(ValueError, match="latency_ms"):
        delayed_arrival_time(submitted_at=_T0, latency_ms=-1)


def test_delayed_arrival_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        delayed_arrival_time(submitted_at=datetime(2026, 1, 1), latency_ms=0)


# --------------------------------------------------------------------------
# BT-5 partial_fill
# --------------------------------------------------------------------------

_PARTIAL = PartialFillConfig(max_participation_pct=Decimal("0.1"))


def test_compute_partial_fill_exact_value_fully_filled() -> None:
    outcome = compute_partial_fill(
        _PARTIAL, order_quantity=Decimal("50"), bar_volume=Decimal("1000")
    )
    assert outcome.filled_quantity == Decimal("50")
    assert outcome.remaining_quantity == Decimal("0")
    assert outcome.is_fully_filled is True


def test_compute_partial_fill_reaches_max_participation_pct_boundary() -> None:
    """order_quantity가 capacity(=bar_volume*max_participation_pct)와
    정확히 같으면 잔량 없이 전량 체결된다."""
    outcome = compute_partial_fill(
        _PARTIAL, order_quantity=Decimal("100"), bar_volume=Decimal("1000")
    )
    assert outcome.filled_quantity == Decimal("100")
    assert outcome.remaining_quantity == Decimal("0")
    assert outcome.is_fully_filled is True


def test_compute_partial_fill_exceeds_cap_leaves_remainder() -> None:
    outcome = compute_partial_fill(
        _PARTIAL, order_quantity=Decimal("150"), bar_volume=Decimal("1000")
    )
    assert outcome.filled_quantity == Decimal("100")
    assert outcome.remaining_quantity == Decimal("50")
    assert outcome.is_fully_filled is False


def test_compute_partial_fill_rejects_negative_order_quantity() -> None:
    with pytest.raises(ValueError, match="order_quantity"):
        compute_partial_fill(_PARTIAL, order_quantity=Decimal("-1"), bar_volume=Decimal("1000"))


def test_compute_partial_fill_rejects_nan_bar_volume() -> None:
    with pytest.raises(ValueError, match="bar_volume"):
        compute_partial_fill(_PARTIAL, order_quantity=Decimal("1"), bar_volume=Decimal("NaN"))


def test_partial_fill_config_rejects_max_participation_pct_above_one() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, contract-level
        PartialFillConfig(max_participation_pct=Decimal("1.01"))


# --------------------------------------------------------------------------
# BT-6 order_types
# --------------------------------------------------------------------------

_ALL_ENABLED = OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True)
_ALL_DISABLED = OrderTypesConfig(limit=False, stop=False, oco=False, trailing=False)


def test_ensure_order_type_enabled_passes_when_enabled() -> None:
    ensure_order_type_enabled(_ALL_ENABLED, "stop")  # must not raise


def test_ensure_order_type_enabled_rejects_disabled_type() -> None:
    with pytest.raises(OrderTypeDisabledError):
        ensure_order_type_enabled(_ALL_DISABLED, "oco")


def test_is_limit_triggered_buy_touches_at_exact_low() -> None:
    triggered = is_limit_triggered(
        side=OrderSide.BUY,
        limit_price=Decimal("100"),
        bar_low=Decimal("100"),
        bar_high=Decimal("105"),
    )
    assert triggered is True


def test_is_limit_triggered_buy_not_touched_above_limit() -> None:
    triggered = is_limit_triggered(
        side=OrderSide.BUY,
        limit_price=Decimal("100"),
        bar_low=Decimal("100.01"),
        bar_high=Decimal("105"),
    )
    assert triggered is False


def test_is_stop_triggered_buy_touches_at_exact_high() -> None:
    """돌파매수 스탑 — 고가가 스탑가에 정확히 닿으면 트리거된다(경계)."""
    triggered = is_stop_triggered(
        side=OrderSide.BUY,
        stop_price=Decimal("110"),
        bar_low=Decimal("100"),
        bar_high=Decimal("110"),
    )
    assert triggered is True


def test_is_stop_triggered_sell_touches_at_exact_low() -> None:
    """손절 스탑 — 저가가 스탑가에 정확히 닿으면 트리거된다(경계)."""
    triggered = is_stop_triggered(
        side=OrderSide.SELL,
        stop_price=Decimal("90"),
        bar_low=Decimal("90"),
        bar_high=Decimal("95"),
    )
    assert triggered is True


def test_is_stop_triggered_sell_not_touched_above_stop() -> None:
    triggered = is_stop_triggered(
        side=OrderSide.SELL,
        stop_price=Decimal("90"),
        bar_low=Decimal("90.01"),
        bar_high=Decimal("95"),
    )
    assert triggered is False


def test_resolve_oco_triggers_a_and_cancels_b() -> None:
    result = resolve_oco(leg_a_triggered=True, leg_b_triggered=False, priority_leg="a")
    assert result == OcoResolution(triggered_leg="a", cancelled_leg="b")


def test_resolve_oco_triggers_b_and_cancels_a() -> None:
    result = resolve_oco(leg_a_triggered=False, leg_b_triggered=True, priority_leg="a")
    assert result == OcoResolution(triggered_leg="b", cancelled_leg="a")


def test_resolve_oco_both_triggered_same_bar_uses_priority_leg() -> None:
    result = resolve_oco(leg_a_triggered=True, leg_b_triggered=True, priority_leg="b")
    assert result == OcoResolution(triggered_leg="b", cancelled_leg="a")


def test_resolve_oco_neither_triggered() -> None:
    result = resolve_oco(leg_a_triggered=False, leg_b_triggered=False, priority_leg="a")
    assert result == OcoResolution(triggered_leg="none", cancelled_leg="none")


def test_update_trailing_stop_long_exit_raises_extreme_on_new_high() -> None:
    state = TrailingStopState(extreme_price=Decimal("100"), stop_price=Decimal("95"))
    updated = update_trailing_stop(
        side=OrderSide.SELL,
        state=state,
        bar_low=Decimal("101"),
        bar_high=Decimal("110"),
        trail_pct=Decimal("0.1"),
    )
    assert updated.extreme_price == Decimal("110")
    assert updated.stop_price == Decimal("99.0")


def test_update_trailing_stop_long_exit_never_lowers_extreme() -> None:
    """새 고점을 못 만들면 극값·스탑이 이전 값을 그대로 유지한다(단조)."""
    state = TrailingStopState(extreme_price=Decimal("110"), stop_price=Decimal("99.0"))
    updated = update_trailing_stop(
        side=OrderSide.SELL,
        state=state,
        bar_low=Decimal("100"),
        bar_high=Decimal("105"),
        trail_pct=Decimal("0.1"),
    )
    assert updated.extreme_price == Decimal("110")
    assert updated.stop_price == Decimal("99.0")


def test_update_trailing_stop_short_exit_lowers_extreme_on_new_low() -> None:
    state = TrailingStopState(extreme_price=Decimal("100"), stop_price=Decimal("105"))
    updated = update_trailing_stop(
        side=OrderSide.BUY,
        state=state,
        bar_low=Decimal("90"),
        bar_high=Decimal("99"),
        trail_pct=Decimal("0.1"),
    )
    assert updated.extreme_price == Decimal("90")
    assert updated.stop_price == Decimal("99.0")


def test_is_limit_triggered_rejects_negative_limit_price() -> None:
    with pytest.raises(ValueError, match="limit_price"):
        is_limit_triggered(
            side=OrderSide.BUY,
            limit_price=Decimal("-1"),
            bar_low=Decimal("1"),
            bar_high=Decimal("2"),
        )


def test_is_stop_triggered_rejects_nan_stop_price() -> None:
    with pytest.raises(ValueError, match="stop_price"):
        is_stop_triggered(
            side=OrderSide.BUY,
            stop_price=Decimal("NaN"),
            bar_low=Decimal("1"),
            bar_high=Decimal("2"),
        )


def test_update_trailing_stop_rejects_negative_trail_pct() -> None:
    state = TrailingStopState(extreme_price=Decimal("100"), stop_price=Decimal("95"))
    with pytest.raises(ValueError, match="trail_pct"):
        update_trailing_stop(
            side=OrderSide.SELL,
            state=state,
            bar_low=Decimal("100"),
            bar_high=Decimal("101"),
            trail_pct=Decimal("-0.1"),
        )


def test_order_types_config_requires_all_four_switches() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, contract-level
        OrderTypesConfig(limit=True, stop=True, oco=True)  # type: ignore[call-arg]
