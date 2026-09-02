"""LB-4 — 펀딩피·체결 수수료(funding_fees).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

무기한 계약의 펀딩은 "포지션 부호 × notional × rate"(§2.3 모듈 표)다.
`qty`는 부호 있는 수량(롱=양수, 숏=음수)이고 `notional = |qty| × mark`이므로
`sign(qty) × notional == qty`가 성립해 `funding = qty × mark.amount × rate`로
계산한다. 부호 관례(양의 rate에서 롱이 지불)는 거래소 문서로 아직 교차
검증하지 않았다 — **미검증**, 실거래소(Bitget) 펀딩 정산 시 반드시 실제
지불/수취 방향과 대조할 것.

체결 수수료·펀딩피를 기준통화로 환산하는 부분은 [[fx.convert]]에
위임한다 — 환율이 없거나 스테일하면 침묵 fallback 없이 예외가 올라간다
([[fx]]와 동일한 taxonomy `POS_FX_RATE_MISSING`). 순수 함수만 —
I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.domain import fx


def funding_amount(qty: Decimal, mark: Money, rate: Decimal) -> Money:
    """무기한 포지션의 펀딩 지불/수취액. 부호는 `qty`를 따른다 — 롱(양수)이
    양의 `rate`에서 지불(음의 현금흐름)하는 관례를 가정한다(미검증)."""
    return Money(amount=qty * mark.amount * rate, currency=mark.currency)


def to_base(
    amount: Money | None,
    base_currency: Currency,
    rate: FXRate | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = fx.DEFAULT_MAX_RATE_AGE,
) -> Decimal:
    """체결 수수료(`fee`) 또는 펀딩 지불액([[funding_amount]] 결과)을
    기준통화 `Decimal`로 환산한다. `amount`가 `None`이면(수수료 없는 체결)
    0을 반환한다 — 이 경우에만 환율을 조회하지 않는다.
    """
    if amount is None:
        return Decimal("0")
    return fx.convert(amount, base_currency, rate, now=now, max_age=max_age).amount
