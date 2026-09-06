"""FA-7 domain/average_price.py — 블록 주문 평균단가 단위테스트(순수 함수만)."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.domain.average_price import (
    PartialFill,
    apply_average_price,
    blended_average_price,
)
from src.foundation.allocation.domain.policy import AllocationLine, AllocationResidualError


def test_blended_average_price_single_fill():
    fills = [PartialFill(quantity=Decimal("100"), price=Decimal("10.00"))]
    price = blended_average_price(fills, Decimal("0.01"))
    assert price == Decimal("10.00")


def test_blended_average_price_weighted_by_quantity():
    fills = [
        PartialFill(quantity=Decimal("60"), price=Decimal("10.00")),
        PartialFill(quantity=Decimal("40"), price=Decimal("11.00")),
    ]
    # (60*10 + 40*11) / 100 = (600+440)/100 = 10.40
    price = blended_average_price(fills, Decimal("0.01"))
    assert price == Decimal("10.40")


def test_blended_average_price_rejects_empty_fills():
    with pytest.raises(AllocationResidualError):
        blended_average_price([], Decimal("0.01"))


def test_blended_average_price_rejects_zero_quantity_fill():
    fills = [PartialFill(quantity=Decimal("0"), price=Decimal("10"))]
    with pytest.raises(AllocationResidualError):
        blended_average_price(fills, Decimal("0.01"))


def test_blended_average_price_rejects_negative_price_fill():
    fills = [PartialFill(quantity=Decimal("10"), price=Decimal("-1"))]
    with pytest.raises(AllocationResidualError):
        blended_average_price(fills, Decimal("0.01"))


def test_apply_average_price_assigns_same_price_to_all_lines():
    a, b = uuid4(), uuid4()
    lines = [
        AllocationLine(sub_account_id=a, quantity=Decimal("60")),
        AllocationLine(sub_account_id=b, quantity=Decimal("40")),
    ]
    result = apply_average_price(
        lines,
        Decimal("10.40"),
        total_notional=Decimal("1040.00"),
        notional_quantum=Decimal("0.01"),
    )
    assert all(line.average_price == Decimal("10.40") for line in result)
    assert {line.sub_account_id for line in result} == {a, b}


def test_apply_average_price_rejects_error_beyond_quantum():
    a = uuid4()
    lines = [AllocationLine(sub_account_id=a, quantity=Decimal("100"))]
    # 100 * 10.40 = 1040.00, 원 명목가치와 0.02 차이 -> quantum(0.01) 초과.
    with pytest.raises(AllocationResidualError):
        apply_average_price(
            lines,
            Decimal("10.40"),
            total_notional=Decimal("1039.98"),
            notional_quantum=Decimal("0.01"),
        )


def test_apply_average_price_accepts_error_within_quantum():
    a = uuid4()
    lines = [AllocationLine(sub_account_id=a, quantity=Decimal("100"))]
    result = apply_average_price(
        lines,
        Decimal("10.40"),
        total_notional=Decimal("1039.99"),
        notional_quantum=Decimal("0.01"),
    )
    assert result[0].average_price == Decimal("10.40")


def test_apply_average_price_rejects_empty_lines():
    with pytest.raises(AllocationResidualError):
        apply_average_price(
            [], Decimal("10"), total_notional=Decimal("0"), notional_quantum=Decimal("0.01")
        )
