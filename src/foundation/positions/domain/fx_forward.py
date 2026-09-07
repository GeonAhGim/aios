"""LB-20 — forward FX / hedge P&L (fx_forward).

Spec: docs/design/ADR-2026-09-06-G-second-audit-corrections.md §9
("FX is spot conversion only — no institutional quoted rates, forward FX,
or hedge P&L"), docs/specs/L4_market_data_positions_ledger_v1.0.md §9.

[[fx.convert]] (LB-4) only does spot conversion. On top of that, this module
(1) computes a forward FX contract's unrealized P&L separately from the
asset's own unrealized FX P&L, (2) derives the residual unhedged exposure,
and (3) attaches the quote's source (`source`) and as-of timestamp
(`known_at`) to the result so it is reproducible. Like `fx.py`, it does not
silently absorb triangulation or tenor mismatches. Pure functions only — no
direct I/O or clock calls.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from src.data.models.base import Currency, Money


class ForwardRateMismatchError(Exception):
    """Tried to revalue with a forward quote for a different (currency
    pair, settlement date) than requested — no substitution/interpolation
    with a quote for a different tenor or currency pair. Follows the same
    principle as `fx.py`'s ban on triangulation."""

    def __init__(self, expected: tuple[Currency, Currency, date], got: ForwardQuote) -> None:
        exp_base, exp_quote, exp_settlement = expected
        super().__init__(
            f"기대: {exp_base.value}/{exp_quote.value} 결제 {exp_settlement.isoformat()}, "
            f"실제: {got.base.value}/{got.quote.value} 결제 {got.settlement_date.isoformat()} "
            f"(source={got.source}, known_at={got.known_at.isoformat()})"
        )
        self.expected = expected
        self.got = got


class HedgeQuoteCurrencyMismatchError(Exception):
    """Tried to sum hedge P&L with the asset's unrealized FX P&L but the
    quote currencies differ — rejected without implicit reconversion."""

    def __init__(self, currencies: set[Currency]) -> None:
        super().__init__(f"단일 표시통화가 아닙니다: {sorted(c.value for c in currencies)}")
        self.currencies = currencies


@dataclass(frozen=True, slots=True)
class ForwardQuote:
    """One forward FX quote.

    Unlike the spot `FXRate` (fx.py), it has a `settlement_date`. `known_at`
    is when this quote was finalized/recorded — kept as a separate field so
    an audit can reproduce "which rate was the calculation based on at that
    point in time" (DoD: "rate source recorded together with known_at,
    reproducible").
    """

    base: Currency
    quote: Currency
    rate: Decimal
    settlement_date: date
    known_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class FxHedgeContract:
    """One forward FX contract used for hedging.

    The sign of `notional` determines direction: positive is a forward sale
    to offset a long `base`-currency exposure (deliver base, receive quote
    at settlement); negative is the opposite (a forward buy). With this sign
    convention, both directions compute as
    `unrealized = notional * (contract_rate - market_rate)`.
    """

    contract_id: str
    base: Currency
    quote: Currency
    notional: Decimal
    contract_rate: Decimal
    settlement_date: date
    known_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class HedgePnl:
    """Result of [[hedge_unrealized_pnl]]. Carries `rate_source` and
    `rate_known_at` so which quote was used is reproducible."""

    contract_id: str
    unrealized: Money
    rate_source: str
    rate_known_at: datetime


def hedge_unrealized_pnl(contract: FxHedgeContract, market: ForwardQuote) -> HedgePnl:
    """Revalues `contract` against the current market forward (`market`) for
    the same settlement date.

    `market`'s `(base, quote, settlement_date)` must match `contract`
    exactly. Raises `ForwardRateMismatchError` on mismatch — no
    interpolation by substituting the nearest tenor.
    """
    expected = (contract.base, contract.quote, contract.settlement_date)
    got = (market.base, market.quote, market.settlement_date)
    if got != expected:
        raise ForwardRateMismatchError(expected, market)

    unrealized = contract.notional * (contract.contract_rate - market.rate)
    return HedgePnl(
        contract_id=contract.contract_id,
        unrealized=Money(amount=unrealized, currency=contract.quote),
        rate_source=market.source,
        rate_known_at=market.known_at,
    )


@dataclass(frozen=True, slots=True)
class FxPnlBreakdown:
    """For NAV decomposition — keeps the asset's own unrealized FX P&L
    separate from hedge unrealized P&L. Their sum, `net`, is informational
    only; both components must always be surfaced separately when NAV is
    displayed (DoD)."""

    currency: Currency
    asset_unrealized_fx: Decimal
    hedge_unrealized_fx: Decimal

    @property
    def net(self) -> Decimal:
        return self.asset_unrealized_fx + self.hedge_unrealized_fx


def decompose_fx_pnl(asset_unrealized_fx: Money, hedge_pnls: Sequence[HedgePnl]) -> FxPnlBreakdown:
    """Aggregates the asset's own unrealized FX P&L and hedge unrealized P&L
    separately, on the same quote currency basis.

    Raises `HedgeQuoteCurrencyMismatchError` if `hedge_pnls` quote
    currencies differ from each other or from
    `asset_unrealized_fx.currency` — rejected without implicit
    reconversion.
    """
    currencies = {p.unrealized.currency for p in hedge_pnls} | {asset_unrealized_fx.currency}
    if len(currencies) > 1:
        raise HedgeQuoteCurrencyMismatchError(currencies)

    hedge_total = sum((p.unrealized.amount for p in hedge_pnls), Decimal("0"))
    return FxPnlBreakdown(
        currency=asset_unrealized_fx.currency,
        asset_unrealized_fx=asset_unrealized_fx.amount,
        hedge_unrealized_fx=hedge_total,
    )


def unhedged_exposure(
    gross_exposure: Decimal,
    hedges: Sequence[FxHedgeContract],
    *,
    base: Currency,
    quote: Currency,
    quantize_to: int | None = None,
) -> Decimal:
    """Computes the unhedged residual of the `base`/`quote` exposure.

    Only contracts in `hedges` whose `(base, quote)` matches exactly are
    reflected in the offset — contracts for other currency pairs are simply
    ignored (silently), never summed in. If `quantize_to` is given, rounds
    to n decimal places (ROUND_HALF_EVEN); if omitted, returns the original
    precision as-is — no implicit rounding.
    """
    hedged = sum(
        (h.notional for h in hedges if h.base == base and h.quote == quote),
        Decimal("0"),
    )
    result = gross_exposure - hedged
    if quantize_to is None:
        return result
    exponent = Decimal("1").scaleb(-quantize_to)
    return result.quantize(exponent, rounding=ROUND_HALF_EVEN)
