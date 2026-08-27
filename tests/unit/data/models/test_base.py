from decimal import Decimal

import pytest

from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import Currency, Money, ProvenanceStatus


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
