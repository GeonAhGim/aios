from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import (
    AssetClass,
    Currency,
    FXRate,
    Money,
    OptionType,
    ProvenanceStatus,
)


def test_provenance_status_values():
    assert ProvenanceStatus.UNVERIFIED == "UNVERIFIED"
    assert set(ProvenanceStatus) == {
        ProvenanceStatus.UNVERIFIED,
        ProvenanceStatus.VERIFIED,
        ProvenanceStatus.DISPUTED,
    }


def test_money_add_same_currency():
    a = Money(amount=Decimal("1.5"), currency=Currency.USDT)
    b = Money(amount=Decimal("2.5"), currency=Currency.USDT)
    result = a + b
    assert result.amount == Decimal("4.0")
    assert result.currency == Currency.USDT


def test_money_add_mismatched_currency_raises():
    a = Money(amount=Decimal("1"), currency=Currency.USDT)
    b = Money(amount=Decimal("1"), currency=Currency.KRW)
    with pytest.raises(CurrencyMismatchError):
        a + b


def test_money_add_boundary_zero_amount():
    a = Money(amount=Decimal("0"), currency=Currency.USDT)
    b = Money(amount=Decimal("0"), currency=Currency.USDT)
    result = a + b
    assert result.amount == Decimal("0")


def test_money_invalid_currency_raises_validation_error():
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), currency="EUR")


def test_money_invalid_amount_type_raises_validation_error():
    with pytest.raises(ValidationError):
        Money(amount="not-a-number", currency=Currency.USDT)


def test_money_missing_required_field_raises_validation_error():
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"))


def test_fxrate_invalid_currency_raises_validation_error():
    with pytest.raises(ValidationError):
        FXRate(
            base="NOT_A_CURRENCY",
            quote=Currency.USDT,
            rate=Decimal("1350.5"),
            timestamp=datetime.now(timezone.utc),
            source="test",
        )


def test_fxrate_missing_timestamp_raises_validation_error():
    with pytest.raises(ValidationError):
        FXRate(
            base=Currency.KRW,
            quote=Currency.USDT,
            rate=Decimal("1350.5"),
            source="test",
        )


def test_asset_class_invalid_member_raises_value_error():
    with pytest.raises(ValueError):
        AssetClass("NOT_A_REAL_ASSET_CLASS")


def test_option_type_invalid_member_raises_value_error():
    with pytest.raises(ValueError):
        OptionType("STRADDLE")


def test_money_add_failure_injection_propagates_arithmetic_error():
    """105 fail-closed: Decimal 덧셈 자체가 실패하면 Money.__add__는 이를 삼키지 않고
    그대로 전파해야 한다 (조용히 잘못된 금액을 반환하지 않음)."""

    class _ExplodingDecimal(Decimal):
        def __add__(self, other):
            raise ArithmeticError("injected failure")

    a = Money(amount=Decimal("1"), currency=Currency.USDT)
    object.__setattr__(a, "amount", _ExplodingDecimal("1"))
    b = Money(amount=Decimal("2"), currency=Currency.USDT)
    with pytest.raises(ArithmeticError):
        a + b
