"""FeeModel 단위테스트 — L4-22."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.base import Currency
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import SimFill


def _fill(liquidity: str = "TAKER", price: str = "100", qty: str = "2") -> SimFill:
    return SimFill(
        price=Decimal(price),
        quantity=Decimal(qty),
        liquidity=liquidity,  # type: ignore[arg-type]
        slippage_bps=Decimal("0"),
        reference_price=Decimal(price),
    )


def _model() -> FeeModel:
    return FeeModel(maker_bps=Decimal("2"), taker_bps=Decimal("6"), fee_currency=Currency.USDT)


def test_taker_fee_uses_taker_bps() -> None:
    money = _model().fee(_fill("TAKER"))
    assert money.amount == Decimal("0.12")  # 200 × 6bps
    assert money.currency == Currency.USDT


def test_maker_fee_uses_maker_bps() -> None:
    assert _model().fee(_fill("MAKER")).amount == Decimal("0.04")


def test_fee_rounds_up_not_in_traders_favor() -> None:
    # 0.000000001 → 0.00000001(ROUND_UP)
    m = FeeModel(maker_bps=Decimal("1"), taker_bps=Decimal("1"), fee_currency=Currency.USDT)
    fee = m.fee(_fill(price="0.000001", qty="0.01"))
    assert fee.amount == Decimal("0.00000001")


def test_zero_bps_gives_zero_fee() -> None:
    m = FeeModel(maker_bps=Decimal("0"), taker_bps=Decimal("0"), fee_currency=Currency.KRW)
    assert m.fee(_fill()).amount == Decimal("0")


def test_negative_bps_rejected() -> None:
    with pytest.raises(ValueError, match="리베이트"):
        FeeModel(maker_bps=Decimal("-1"), taker_bps=Decimal("1"), fee_currency=Currency.USDT)


def test_non_positive_fill_rejected() -> None:
    with pytest.raises(ValueError):
        _model().fee(_fill(qty="0"))
