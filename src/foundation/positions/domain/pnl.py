"""LB-4 — 실현/미실현 PnL 분해(pnl).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

미실현 PnL은 `(mark − avg_cost) × qty × multiplier`이고, 기준통화(base
currency)로의 환산은 [[fx.convert]]에 위임한다 — 환율이 없거나 스테일하면
[[fx]]가 예외를 던지므로 이 모듈은 절대 0으로 대체하지 않는다. 실현
PnL·수수료·펀딩피는 스냅샷([[../contracts/v1.py]] `PositionSnapshotView`)에
이미 기준통화로 누적된 값(`realized_pnl_base`/`fees_base`/`funding_base`)을
그대로 사용한다 — 이 리프가 다시 환산하지 않는다(원천 환산은 각 저널행
기록 시점의 [[fx]]/[[funding_fees]] 호출이 책임진다).

`PnLBreakdown.total`은 네 구성요소의 **정확한 대수적 합**이다(LC-2
`rounding.split_commission`의 "합 보존" 원칙과 동일한 정신 — 구성요소를
독립적으로 반올림해 잔차를 만들지 않는다). §3.4에 따라 기준통화 PnL 금액은
저장 시 절대 반올림하지 않으므로, 이 함수도 반올림/quantize를 하지
않는다 — Decimal 그대로 합산한다. 순수 함수만 — I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.core.exceptions import CurrencyMismatchError
from src.data.models.base import FXRate, Money
from src.foundation.positions.contracts.v1 import PnLBreakdown, PositionSnapshotView
from src.foundation.positions.domain import fx


def unrealized(
    snapshot: PositionSnapshotView,
    mark: Money,
    rate: FXRate | None,
    *,
    contract_multiplier: Decimal = Decimal("1"),
    now: datetime | None = None,
    max_age: timedelta = fx.DEFAULT_MAX_RATE_AGE,
) -> PnLBreakdown:
    """스냅샷의 실현/수수료/펀딩(이미 기준통화)에 방금 조회한 마크가격으로
    계산한 미실현을 더해 `PnLBreakdown`을 만든다.

    `mark`는 `snapshot.avg_cost`와 같은 통화여야 한다(같은 계좌·상품의
    평단·마크가 다른 통화로 들어오면 호출부 버그 — `CurrencyMismatchError`).
    수량이 0(포지션 없음)이면 미실현은 0이고 환율은 조회하지 않는다 — 열린
    수량이 없는데 없는/스테일한 환율 때문에 실패하지 않도록 한다.
    """
    if snapshot.quantity == 0:
        unrealized_amount = Decimal("0")
        fx_rates_used: list[FXRate] = []
    else:
        if mark.currency != snapshot.avg_cost.currency:
            raise CurrencyMismatchError(mark.currency, snapshot.avg_cost.currency)

        price_diff = mark.amount - snapshot.avg_cost.amount
        raw = Money(
            amount=price_diff * snapshot.quantity * contract_multiplier,
            currency=mark.currency,
        )
        converted = fx.convert(raw, snapshot.base_currency, rate, now=now, max_age=max_age)
        unrealized_amount = converted.amount
        fx_rates_used = [converted.rate] if converted.rate is not None else []

    total = (
        snapshot.realized_pnl_base
        + unrealized_amount
        + snapshot.fees_base
        + snapshot.funding_base
    )

    return PnLBreakdown(
        realized=snapshot.realized_pnl_base,
        unrealized=unrealized_amount,
        fees=snapshot.fees_base,
        funding=snapshot.funding_base,
        total=total,
        base_currency=snapshot.base_currency,
        fx_rates_used=fx_rates_used,
    )
