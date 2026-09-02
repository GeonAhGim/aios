"""LB-4 — 통화 환산(fx).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

`Money`를 기준통화(base currency)로 환산한다. 환율이 없거나 스테일하면
침묵 fallback(0 대체) 없이 명시적으로 예외를 던진다 — §3.2 에러 taxonomy
`POS_FX_RATE_MISSING`("가능, 환율 도착 후 — 0으로 대체 금지")을
`PositionErrorCode`에서 그대로 재사용한다. 삼각환산은 금지: 넘어온
`FXRate`가 `(m.currency, to)` 쌍을 직접(base→quote) 또는 역방향
(quote→base)으로 표현하지 않으면 미존재로 취급한다 — 체인된 환율은
호출자가 미리 직접 조회해야 한다. 순수 함수만 — I/O·시계 직접 호출 금지,
staleness 판정은 호출자가 `now`를 인자로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.contracts.v1 import PositionErrorCode

DEFAULT_MAX_RATE_AGE = timedelta(minutes=5)


class FxRateMissingError(Exception):
    """`POS_FX_RATE_MISSING` — 요청한 통화쌍의 환율이 없다(재시도 가능,
    환율 도착 후). 0으로 대체하지 않는다."""

    code = PositionErrorCode.FX_RATE_MISSING

    def __init__(self, source: Currency, target: Currency, *, message: str | None = None) -> None:
        super().__init__(message or f"{source.value}->{target.value}: 사용 가능한 환율이 없습니다.")
        self.source = source
        self.target = target


class FxRateStaleError(FxRateMissingError):
    """`POS_FX_RATE_MISSING`과 같은 재시도 등급 — 환율은 존재하나
    `max_age`보다 오래됐다. 스테일 환율로 조용히 계속 진행하지 않는다."""

    def __init__(self, rate: FXRate, *, now: datetime, max_age: timedelta) -> None:
        age = now - rate.timestamp
        super().__init__(
            rate.base,
            rate.quote,
            message=(
                f"{rate.base.value}->{rate.quote.value}: 환율이 스테일합니다"
                f"(age={age}, max_age={max_age}, source={rate.source})."
            ),
        )
        self.age = age
        self.max_age = max_age
        self.rate = rate


@dataclass(frozen=True, slots=True)
class Converted:
    """환산 결과. `rate`는 실제로 통화가 바뀐 경우에만 채워진다(동일 통화는
    `rate=None`) — 결과에 환율 출처·시각을 동봉하라는 §3.2 요구를 만족한다."""

    amount: Decimal
    currency: Currency
    rate: FXRate | None


def convert(
    m: Money,
    to: Currency,
    rate: FXRate | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_RATE_AGE,
) -> Converted:
    """`m`을 `to` 통화로 환산한다.

    - `m.currency == to`면 환율 없이 그대로 반환한다.
    - 아니면 `rate`가 있어야 하고, `(rate.base, rate.quote)`가
      `(m.currency, to)`(정방향) 또는 `(to, m.currency)`(역방향)와 정확히
      일치해야 한다. 그 외(다른 통화쌍, 즉 삼각환산이 필요한 경우)는
      미존재로 취급한다.
    - `now`가 주어지면 `rate.timestamp`가 `max_age`보다 오래된 경우
      `FxRateStaleError`를 던진다(`now` 생략 시 staleness 검사를 건너뛴다 —
      호출자가 시계를 갖고 있지 않은 순수 계산 컨텍스트를 위함).
    """
    if m.currency == to:
        return Converted(amount=m.amount, currency=to, rate=None)

    if rate is None:
        raise FxRateMissingError(m.currency, to)

    if now is not None and (now - rate.timestamp) > max_age:
        raise FxRateStaleError(rate, now=now, max_age=max_age)

    if rate.base == m.currency and rate.quote == to:
        amount = m.amount * rate.rate
    elif rate.base == to and rate.quote == m.currency:
        if rate.rate == 0:
            raise FxRateMissingError(m.currency, to)
        amount = m.amount / rate.rate
    else:
        raise FxRateMissingError(m.currency, to)

    return Converted(amount=amount, currency=to, rate=rate)
