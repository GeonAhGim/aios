"""LC-2 — 합 보존 반올림.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3 (C), §9 LC-2.

원장 금액(KRW)은 `NUMERIC(20,2)`, `ROUND_HALF_EVEN`(§3.3). 커미션은
`price × rate`를 반올림한 값이고, 판매자 정산액은 나머지(`price − commission`)로
계산한다 — 이렇게 정산액을 반올림 결과의 나머지로 정의해야 두 값의 합이
분배 후에도 항상 `price`와 정확히 일치한다(잔차 없음).
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_KRW_QUANTUM = Decimal("0.01")


def split_commission(price: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """(commission, payout)을 반환한다. `commission + payout == price`가 항상 성립."""
    commission = (price * rate).quantize(_KRW_QUANTUM, rounding=ROUND_HALF_EVEN)
    payout = price - commission
    return commission, payout
