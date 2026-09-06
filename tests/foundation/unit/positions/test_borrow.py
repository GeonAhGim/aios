"""LA-25 — borrow(공매도 차입·마진) 단위테스트.

Spec: ADR-2026-09-06-G §9 ("공매도·차입·마진 개념 자체가 없다(locate
없음)"). DoD 3항목을 각각 검증한다: (1) 일별 차입 이자 적립,
(2) 마진콜 임계 돌파 3개 픽스처 시나리오 누락 없이 알림,
(3) locate 없는 공매도 주문 게이트 거부.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.domain import borrow

_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def _locate(
    *,
    quantity: str = "100",
    granted_before: timedelta = timedelta(days=1),
    expires_after: timedelta = timedelta(days=1),
    locate_id: str = "LOC-1",
) -> borrow.Locate:
    return borrow.Locate(
        locate_id=locate_id,
        quantity=Decimal(quantity),
        source="prime-broker",
        granted_at=_NOW - granted_before,
        expires_at=_NOW + expires_after,
    )


def _position(
    *, short_quantity: str = "100", supply_rate: str = "0.03", currency: Currency = Currency.USDT
) -> borrow.BorrowPosition:
    return borrow.BorrowPosition(
        position_key="BITGET:BTC:s1:e1",
        short_quantity=Decimal(short_quantity),
        supply_rate=Decimal(supply_rate),
        currency=currency,
    )


# --- Locate 값 객체 ----------------------------------------------------


def test_locate_rejects_non_positive_quantity() -> None:
    with pytest.raises(borrow.NonPositiveQuantityError):
        _locate(quantity="0")


def test_locate_rejects_expires_before_granted() -> None:
    with pytest.raises(ValueError):
        borrow.Locate(
            locate_id="LOC-X",
            quantity=Decimal("1"),
            source="prime-broker",
            granted_at=_NOW,
            expires_at=_NOW - timedelta(seconds=1),
        )


def test_locate_is_active_respects_window() -> None:
    loc = _locate(granted_before=timedelta(hours=1), expires_after=timedelta(hours=1))

    assert loc.is_active(as_of=_NOW) is True
    assert loc.is_active(as_of=_NOW - timedelta(hours=2)) is False
    assert loc.is_active(as_of=_NOW + timedelta(hours=2)) is False


# --- DoD (3): locate 게이트 ---------------------------------------------


def test_check_locate_gate_allows_buy_without_locate() -> None:
    borrow.check_locate_gate(
        side=OrderSide.BUY,
        quantity=Decimal("50"),
        current_position_quantity=Decimal("0"),
        locates=[],
        as_of=_NOW,
    )  # 예외 없음이 통과 조건


def test_check_locate_gate_allows_long_liquidation_sell_without_locate() -> None:
    """기존 롱 100주를 청산하는 매도는 공매도가 아니므로 locate 불필요."""
    borrow.check_locate_gate(
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        current_position_quantity=Decimal("100"),
        locates=[],
        as_of=_NOW,
    )


def test_check_locate_gate_rejects_short_sell_without_locate() -> None:
    """locate 없는 신규 공매도는 게이트에서 거부돼야 한다(DoD)."""
    with pytest.raises(borrow.LocateRequiredError) as exc_info:
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            current_position_quantity=Decimal("0"),
            locates=[],
            as_of=_NOW,
        )

    assert exc_info.value.requested == Decimal("100")
    assert exc_info.value.available == Decimal("0")


def test_check_locate_gate_rejects_short_sell_with_insufficient_locate() -> None:
    with pytest.raises(borrow.LocateRequiredError):
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            current_position_quantity=Decimal("0"),
            locates=[_locate(quantity="40")],
            as_of=_NOW,
        )


def test_check_locate_gate_allows_short_sell_with_sufficient_locate() -> None:
    borrow.check_locate_gate(
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        current_position_quantity=Decimal("0"),
        locates=[_locate(quantity="60"), _locate(quantity="40", locate_id="LOC-2")],
        as_of=_NOW,
    )


def test_check_locate_gate_ignores_expired_locate() -> None:
    expired = _locate(granted_before=timedelta(days=2), expires_after=-timedelta(days=1))

    with pytest.raises(borrow.LocateRequiredError):
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            current_position_quantity=Decimal("0"),
            locates=[expired],
            as_of=_NOW,
        )


def test_check_locate_gate_sell_across_zero_only_needs_locate_for_new_short_part() -> None:
    """롱 50주 보유 중 80주를 매도하면 50주는 청산(공매도 아님), 나머지
    30주만 신규 공매도다 — locate는 30주분만 있으면 충분하다."""
    borrow.check_locate_gate(
        side=OrderSide.SELL,
        quantity=Decimal("80"),
        current_position_quantity=Decimal("50"),
        locates=[_locate(quantity="30")],
        as_of=_NOW,
    )

    with pytest.raises(borrow.LocateRequiredError) as exc_info:
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("80"),
            current_position_quantity=Decimal("50"),
            locates=[_locate(quantity="29")],
            as_of=_NOW,
        )
    assert exc_info.value.requested == Decimal("30")


def test_check_locate_gate_rejects_increasing_existing_short_beyond_locate() -> None:
    with pytest.raises(borrow.LocateRequiredError) as exc_info:
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("50"),
            current_position_quantity=Decimal("-70"),
            locates=[_locate(quantity="30")],
            as_of=_NOW,
        )

    assert exc_info.value.requested == Decimal("50")
    assert exc_info.value.available == Decimal("30")


def test_check_locate_gate_rejects_non_positive_quantity() -> None:
    with pytest.raises(borrow.NonPositiveQuantityError):
        borrow.check_locate_gate(
            side=OrderSide.SELL,
            quantity=Decimal("0"),
            current_position_quantity=Decimal("0"),
            locates=[],
            as_of=_NOW,
        )


# --- DoD (1): 일별 차입 이자 적립 ----------------------------------------


def test_accrue_daily_interest_matches_formula() -> None:
    position = _position(short_quantity="100", supply_rate="0.036")
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)

    result = borrow.accrue_daily_interest(position, mark_price=mark, day_count=360)

    # 100 * 50 * 0.036 / 360 = 0.5
    assert result.amount == Decimal("0.5")
    assert result.currency == Currency.USDT


def test_accrue_daily_interest_rejects_currency_mismatch() -> None:
    position = _position(currency=Currency.USDT)
    mark = Money(amount=Decimal("50"), currency=Currency.KRW)

    with pytest.raises(borrow.CurrencyMismatchError):
        borrow.accrue_daily_interest(position, mark_price=mark)


def test_accrue_daily_interest_rejects_non_positive_day_count() -> None:
    position = _position()
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)

    with pytest.raises(borrow.NonPositiveQuantityError):
        borrow.accrue_daily_interest(position, mark_price=mark, day_count=0)


def test_accrue_interest_over_sums_daily_amounts_order_independent() -> None:
    position = _position(short_quantity="100", supply_rate="0.036")
    marks = [
        Money(amount=Decimal("50"), currency=Currency.USDT),
        Money(amount=Decimal("60"), currency=Currency.USDT),
        Money(amount=Decimal("40"), currency=Currency.USDT),
    ]

    total = borrow.accrue_interest_over(position, daily_marks=marks, day_count=360)
    total_reversed = borrow.accrue_interest_over(
        position, daily_marks=list(reversed(marks)), day_count=360
    )

    # (100*50 + 100*60 + 100*40) * 0.036 / 360 = 1.5
    assert total.amount == Decimal("1.5")
    assert total.amount == total_reversed.amount


def test_borrow_position_rejects_non_positive_short_quantity() -> None:
    with pytest.raises(borrow.NonPositiveQuantityError):
        _position(short_quantity="0")


def test_borrow_position_rejects_negative_supply_rate() -> None:
    with pytest.raises(ValueError):
        _position(supply_rate="-0.01")


# --- DoD (2): 마진콜 임계 돌파 3개 픽스처 시나리오 ------------------------


def test_margin_call_scenario_healthy_equity_no_alert() -> None:
    """시나리오 1: 담보가 충분해 유지증거금을 넉넉히 넘는다 — 알림 없음."""
    position = _position(short_quantity="100", supply_rate="0.03")
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)
    collateral = Money(amount=Decimal("8000"), currency=Currency.USDT)

    event = borrow.evaluate_margin_call(
        position,
        mark_price=mark,
        collateral=collateral,
        maintenance_margin_rate=Decimal("0.3"),
        as_of=_NOW,
    )

    assert event is None


def test_margin_call_scenario_price_spike_breaches_maintenance_margin() -> None:
    """시나리오 2: 숏 대상 가격이 급등해 자기자본이 유지증거금 아래로
    떨어진다 — 알림이 누락 없이 발생해야 한다."""
    position = _position(short_quantity="100", supply_rate="0.03")
    mark = Money(amount=Decimal("90"), currency=Currency.USDT)  # 시가 급등
    collateral = Money(amount=Decimal("6000"), currency=Currency.USDT)

    event = borrow.evaluate_margin_call(
        position,
        mark_price=mark,
        collateral=collateral,
        maintenance_margin_rate=Decimal("0.3"),
        as_of=_NOW,
    )

    assert event is not None
    # equity = 6000 - 100*90 = -3000; required = 0.3*9000 = 2700; deficit = 5700
    assert event.equity == Decimal("-3000")
    assert event.required_margin == Decimal("2700")
    assert event.deficit == Decimal("5700")
    assert event.position_key == position.position_key
    assert event.as_of == _NOW


def test_margin_call_scenario_thin_collateral_breaches_immediately() -> None:
    """시나리오 3: 가격은 그대로지만 담보가 애초에 유지증거금 요건보다
    적어 즉시 위반이다 — 알림이 누락 없이 발생해야 한다."""
    position = _position(short_quantity="200", supply_rate="0.03")
    mark = Money(amount=Decimal("30"), currency=Currency.USDT)
    collateral = Money(amount=Decimal("1000"), currency=Currency.USDT)

    event = borrow.evaluate_margin_call(
        position,
        mark_price=mark,
        collateral=collateral,
        maintenance_margin_rate=Decimal("0.25"),
        as_of=_NOW,
    )

    assert event is not None
    # equity = 1000 - 200*30 = -5000; required = 0.25*6000 = 1500; deficit = 6500
    assert event.equity == Decimal("-5000")
    assert event.required_margin == Decimal("1500")
    assert event.deficit == Decimal("6500")


def test_margin_call_exact_threshold_is_not_a_breach() -> None:
    """경계값: equity == required_margin이면 요건을 정확히 충족한 것이라
    위반이 아니다(알림 없음)."""
    position = _position(short_quantity="100", supply_rate="0.03")
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)
    # required = 0.3 * 100*50 = 1500; equity == 1500 이 되도록 담보 설정
    collateral = Money(amount=Decimal("6500"), currency=Currency.USDT)

    event = borrow.evaluate_margin_call(
        position,
        mark_price=mark,
        collateral=collateral,
        maintenance_margin_rate=Decimal("0.3"),
        as_of=_NOW,
    )

    assert event is None


def test_margin_call_rejects_non_positive_maintenance_rate() -> None:
    position = _position()
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)
    collateral = Money(amount=Decimal("1000"), currency=Currency.USDT)

    with pytest.raises(borrow.NonPositiveQuantityError):
        borrow.evaluate_margin_call(
            position,
            mark_price=mark,
            collateral=collateral,
            maintenance_margin_rate=Decimal("0"),
            as_of=_NOW,
        )


def test_margin_call_rejects_currency_mismatch_on_collateral() -> None:
    position = _position(currency=Currency.USDT)
    mark = Money(amount=Decimal("50"), currency=Currency.USDT)
    collateral = Money(amount=Decimal("1000"), currency=Currency.KRW)

    with pytest.raises(borrow.CurrencyMismatchError):
        borrow.evaluate_margin_call(
            position,
            mark_price=mark,
            collateral=collateral,
            maintenance_margin_rate=Decimal("0.3"),
            as_of=_NOW,
        )
