"""LB-4 — realized/unrealized PnL breakdown (pnl).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

Unrealized PnL is `(mark - avg_cost) x qty x multiplier`, and conversion to
the base currency is delegated to [[fx.convert]] — if the rate is missing or
stale, [[fx]] raises, so this module never substitutes 0. Realized PnL,
fees, and funding fees use the values already accumulated in the base
currency on the snapshot ([[../contracts/v1.py]] `PositionSnapshotView`)
(`realized_pnl_base`/`fees_base`/`funding_base`) as-is — this leaf does not
convert them again (the source conversion is the responsibility of the
[[fx]]/[[funding_fees]] calls made at the time each journal row was
recorded).

`PnLBreakdown.total` is the **exact algebraic sum** of the four components
(same spirit as the "sum preservation" principle of LC-2
`rounding.split_commission` — components are not independently rounded in a
way that creates a residual). Per §3.4, base-currency PnL amounts are never
rounded at storage time, so this function does not round/quantize either —
it sums the raw Decimals. Pure function only — no I/O or direct clock calls.
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
    """Build a `PnLBreakdown` by adding the unrealized amount computed from
    the just-fetched mark price to the snapshot's realized/fees/funding
    (already in base currency).

    `mark` must be in the same currency as `snapshot.avg_cost` (if the
    avg cost and mark of the same account/instrument arrive in different
    currencies, that is a caller bug — `CurrencyMismatchError`). If the
    quantity is 0 (no position), unrealized is 0 and the rate is not looked
    up — so a missing/stale rate does not fail a call with no open quantity.
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
