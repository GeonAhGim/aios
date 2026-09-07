"""LA-25 — short-sale borrow/margin primitives.

Spec: ADR-2026-09-06-G §9 table ("no short-sale/borrow/margin concept at all
(no locate) — Aladdin/CRIMS block short sales without a locate" → "LA-25
`positions/domain/borrow.py` + `pos_borrow_position`"). This leaf is not yet
reflected in `docs/specs/L4_market_data_positions_ledger_v1.0.md` §9 table
(that doc's §10 R6 only mentions "cash short-sale prohibited") — the contract
is defined here for the first time based on the one-line description in the
ADR-G body. The persistence table `pos_borrow_position` is not created yet
in this leaf because it is waiting on a migration parent (down_revision)
decision (PM decision) — below is pure domain rules only; the repository
adapter is a later leaf's job.

Three primitives (§DoD):
1. **Locate gate** — a short sale (a sell that pushes net position further
   short) cannot exceed the quantity of ownership confirmations (locates)
   secured in advance (`check_locate_gate`). A violation raises
   `LocateRequiredError` (non-retriable; the caller must secure a locate
   first).
2. **Daily borrow interest accrual** — short quantity x mark price x supply
   rate (annual) / day-count. The day-count convention (ACT/360 vs ACT/365)
   is **unverified** pending cross-checking against the prime broker
   agreement — `DEFAULT_DAY_COUNT=360` is an assumption.
3. **Margin call determination** — a `MarginCallEvent` is raised when equity
   (collateral - short market value) falls below the maintenance margin
   requirement (maintenance margin rate x short market value). Real prime
   brokerage margin detail (Reg T, portfolio margin), including short-sale
   proceeds reinvestment/interest, is out of scope for this primitive —
   **unverified**, pending cross-checking against actual broker margin
   rules.

Pure functions and value objects only — no direct I/O or clock calls (the
caller passes `as_of`). `Decimal` only, no `float` (same principle as
standard 105).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderSide

DEFAULT_DAY_COUNT = 360
"""Days divisor for pro-rating the annual rate. Assumes the ACT/360
convention (unverified — pending cross-checking against the actual prime
broker agreement, see docstring above)."""


class NonPositiveQuantityError(ValueError):
    """A value that must be positive (quantity, rate, ...) is <= 0."""


class CurrencyMismatchError(ValueError):
    """Tried to mix `Money` of different currencies in the same calculation."""

    def __init__(self, expected: Currency, actual: Currency) -> None:
        super().__init__(f"통화 불일치: 기대={expected.value}, 실제={actual.value}")
        self.expected = expected
        self.actual = actual


class LocateRequiredError(Exception):
    """A short-sale order exceeds the secured locate quantity — order gate
    rejection.

    Non-retriable: the caller must secure an additional locate before
    resubmitting. The same gate Aladdin/CRIMS use to block short sales
    without a locate at the source (ADR-2026-09-06-G §9)."""

    def __init__(self, *, requested: Decimal, available: Decimal) -> None:
        super().__init__(
            f"locate 부족: 필요 숏 수량 {requested} > 확보된 locate {available} "
            "— 공매도 주문을 게이트에서 거부합니다."
        )
        self.requested = requested
        self.available = available


@dataclass(frozen=True, slots=True)
class Locate:
    """One ownership confirmation (locate) secured before a short sale."""

    locate_id: str
    quantity: Decimal
    source: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise NonPositiveQuantityError(f"quantity는 0보다 커야 합니다: {self.quantity}")
        if self.expires_at <= self.granted_at:
            raise ValueError("expires_at은 granted_at 이후여야 합니다.")

    def is_active(self, *, as_of: datetime) -> bool:
        return self.granted_at <= as_of < self.expires_at


def check_locate_gate(
    *,
    side: OrderSide,
    quantity: Decimal,
    current_position_quantity: Decimal,
    locates: list[Locate],
    as_of: datetime,
) -> None:
    """Short-sale order gate. Pure determination — returns `None` and ends
    silently on pass; raises on violation (non-retriable, no silent pass).

    `side == BUY` always passes (a short cover or long entry needs no
    locate). For `side == SELL`, only the amount by which the post-fill net
    position (`current_position_quantity - quantity`, negative = short)
    grows more short than before (= the newly created short) must be
    covered by a locate — a sell that liquidates an existing long holding
    is not a short sale and needs no locate.

    Only the sum of locates active (past grant, before expiry) at `as_of`
    counts as available.
    """
    if quantity <= 0:
        raise NonPositiveQuantityError(f"quantity는 0보다 커야 합니다: {quantity}")
    if side != OrderSide.SELL:
        return

    resulting_quantity = current_position_quantity - quantity
    previous_short = max(Decimal("0"), -current_position_quantity)
    resulting_short = max(Decimal("0"), -resulting_quantity)
    new_short_quantity = resulting_short - previous_short
    if new_short_quantity <= 0:
        return  # liquidating an existing long or reducing a short — not a short sale.

    available = sum((loc.quantity for loc in locates if loc.is_active(as_of=as_of)), Decimal("0"))
    if available < new_short_quantity:
        raise LocateRequiredError(requested=new_short_quantity, available=available)


@dataclass(frozen=True, slots=True)
class BorrowPosition:
    """Pure view of one `pos_borrow_position` row (persistence is a later
    leaf). Short quantity is always stored positive — sign is the
    responsibility of `PositionSnapshotView.quantity` (negative for short)
    outside this value object."""

    position_key: str
    short_quantity: Decimal
    supply_rate: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if self.short_quantity <= 0:
            raise NonPositiveQuantityError(
                f"short_quantity는 0보다 커야 합니다: {self.short_quantity}"
            )
        if self.supply_rate < 0:
            raise ValueError(f"supply_rate는 음수일 수 없습니다: {self.supply_rate}")


def _require_currency(money: Money, expected: Currency) -> None:
    if money.currency != expected:
        raise CurrencyMismatchError(expected, money.currency)


def accrue_daily_interest(
    position: BorrowPosition,
    *,
    mark_price: Money,
    day_count: int = DEFAULT_DAY_COUNT,
) -> Money:
    """One day's borrow interest on the short position = `short_quantity x
    mark_price x supply_rate / day_count` (accrued at the supply rate,
    always positive — a cost the borrower pays to the lending institution).
    `day_count` must be > 0."""
    if day_count <= 0:
        raise NonPositiveQuantityError(f"day_count는 0보다 커야 합니다: {day_count}")
    _require_currency(mark_price, position.currency)
    daily = position.short_quantity * mark_price.amount * position.supply_rate / Decimal(day_count)
    return Money(amount=daily, currency=position.currency)


def accrue_interest_over(
    position: BorrowPosition,
    *,
    daily_marks: list[Money],
    day_count: int = DEFAULT_DAY_COUNT,
) -> Money:
    """Sum of daily interest across multiple days (recomputed with each
    day's mark price) — addition is commutative/associative, so order
    doesn't matter (fold)."""
    total = Decimal("0")
    for mark in daily_marks:
        total += accrue_daily_interest(position, mark_price=mark, day_count=day_count).amount
    return Money(amount=total, currency=position.currency)


@dataclass(frozen=True, slots=True)
class MarginCallEvent:
    """Margin call notification event — one maintenance-margin violation."""

    position_key: str
    equity: Decimal
    required_margin: Decimal
    deficit: Decimal
    currency: Currency
    as_of: datetime


def evaluate_margin_call(
    position: BorrowPosition,
    *,
    mark_price: Money,
    collateral: Money,
    maintenance_margin_rate: Decimal,
    as_of: datetime,
) -> MarginCallEvent | None:
    """Determine a maintenance-margin violation on a short position.

    `equity = collateral - short_quantity x mark_price` (simplified model —
    short-sale proceeds reinvestment/interest is out of scope, see docstring
    above). `required_margin = maintenance_margin_rate x short_quantity x
    mark_price`. If `equity < required_margin`, returns a `MarginCallEvent`
    with the shortfall (`deficit`, always positive) — the boundary
    (`equity == required_margin`) is not a violation (requirement exactly
    met). Returns `None` (no notification, silent success) if not a
    violation.
    """
    if maintenance_margin_rate <= 0:
        raise NonPositiveQuantityError(
            f"maintenance_margin_rate는 0보다 커야 합니다: {maintenance_margin_rate}"
        )
    _require_currency(mark_price, position.currency)
    _require_currency(collateral, position.currency)

    market_value = position.short_quantity * mark_price.amount
    equity = collateral.amount - market_value
    required_margin = maintenance_margin_rate * market_value
    if equity >= required_margin:
        return None

    return MarginCallEvent(
        position_key=position.position_key,
        equity=equity,
        required_margin=required_margin,
        deficit=required_margin - equity,
        currency=position.currency,
        as_of=as_of,
    )
