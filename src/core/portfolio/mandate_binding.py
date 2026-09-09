"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 96, section 9 L20 --
mandate_binding.py.

Applies a `MandateRevisionView`'s constraints to a proposed order quantity,
in the fixed order the spec mandates (line 317): FORBIDDEN_ASSET denial,
then MAX_SINGLE_INSTRUMENT -> MAX_TOTAL_EXPOSURE -> MIN_CASH_BUFFER clamps
in that sequence. The order is hardcoded rather than data-driven because
changing it changes the result when more than one constraint binds at once.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.portfolio.state_input import PortfolioAggregate
from src.foundation.mandates.contracts.v1 import MandateRevisionView

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# Same string values as
# `src.foundation.mandates.domain.rules.evaluate_policy` -- reused as-is,
# never redefined with new semantics (task DoD).
POLICY_FORBIDDEN_ASSET = "POLICY_FORBIDDEN_ASSET"
POLICY_MAX_SINGLE_INSTRUMENT = "POLICY_MAX_SINGLE_INSTRUMENT"
POLICY_MAX_TOTAL_EXPOSURE = "POLICY_MAX_TOTAL_EXPOSURE"
POLICY_MIN_CASH_BUFFER = "POLICY_MIN_CASH_BUFFER"


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float is not accepted here -- pass Decimal")
    return value


class MandateRevisionHashMismatchError(ValueError):
    """The `MandateRevisionView` handed to `bind()` does not hash to its own
    `revision_hash` -- some field was altered after the view left the
    mandate service (e.g. a wiped `forbidden_assets`). Fail-closed via a
    raised exception, never surfaced as an approvable `BindingResult`.
    """


class BindingResult(BaseModel):
    quantity: Decimal
    reasons: list[str]
    denied: bool

    @field_validator("quantity", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)


def _recompute_revision_hash(mandate: MandateRevisionView) -> str:
    """Bit-for-bit mirror of
    `src.foundation.mandates.domain.rules.compute_revision_hash` (MAN-001).

    Duplicated rather than imported: that function's parameter type is the
    domain dataclass `MandateRevision`, and `contracts/v1.py` documents that
    other bounded contexts consume the contract, not `domain/models.py`.
    Both must be kept in sync by hand if the hash recipe ever changes.
    """
    payload = "|".join(
        [
            f"{mandate.max_total_exposure_pct:.2f}",
            f"{mandate.max_single_instrument_pct:.2f}",
            f"{mandate.min_cash_buffer_pct:.2f}",
            f"{mandate.max_daily_loss_pct:.2f}",
            mandate.allowed_autonomy.value,
            ",".join(sorted(mandate.forbidden_assets)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _denied(reasons: list[str]) -> BindingResult:
    return BindingResult(quantity=_ZERO, reasons=reasons, denied=True)


def _is_unsafe_input(qty: Decimal, price: Decimal, total_equity: Decimal) -> bool:
    """Non-positive or NaN economics make every clamp below meaningless (a
    non-positive price could make `qty * price` look arbitrarily small and
    slip past every upper-bound check, and NaN raises on any comparison).
    Caught once, up front, instead of guarding every arithmetic step below;
    routes to the first constraint in the pipeline (`MAX_SINGLE_INSTRUMENT`)
    since that is the earliest gate a real order would hit.
    """
    if qty.is_nan() or price.is_nan() or total_equity.is_nan():
        return True
    return qty <= 0 or price <= 0 or total_equity <= 0


def bind(
    qty: Decimal,
    price: Decimal,
    symbol: str,
    agg: PortfolioAggregate,
    mandate: MandateRevisionView,
) -> BindingResult:
    """section 9 L20 -- sequential clamp: single-instrument -> total-exposure
    -> cash-buffer. A clamp that reaches exactly 0 denies the whole order
    (no zero-quantity approval); `reasons` accumulates every clamp that
    actually fired, in this fixed pipeline order.
    """
    if _recompute_revision_hash(mandate) != mandate.revision_hash:
        raise MandateRevisionHashMismatchError(
            f"revision {mandate.id} hash mismatch -- view was altered after issuance"
        )

    if symbol in mandate.forbidden_assets:
        return _denied([POLICY_FORBIDDEN_ASSET])

    if _is_unsafe_input(qty, price, agg.total_equity):
        return _denied([POLICY_MAX_SINGLE_INSTRUMENT])

    total_equity = agg.total_equity
    working_qty = qty
    reasons: list[str] = []

    single_limit = Decimal(str(mandate.max_single_instrument_pct)) / _HUNDRED * total_equity
    single_existing = agg.per_symbol_pct.get(symbol, _ZERO) / _HUNDRED * total_equity
    single_allowed_qty = max(single_limit - single_existing, _ZERO) / price
    if working_qty > single_allowed_qty:
        working_qty = single_allowed_qty
        reasons.append(POLICY_MAX_SINGLE_INSTRUMENT)

    total_limit = Decimal(str(mandate.max_total_exposure_pct)) / _HUNDRED * total_equity
    total_existing = agg.total_exposure_pct / _HUNDRED * total_equity
    total_allowed_qty = max(total_limit - total_existing, _ZERO) / price
    if working_qty > total_allowed_qty:
        working_qty = total_allowed_qty
        reasons.append(POLICY_MAX_TOTAL_EXPOSURE)

    existing_cash = agg.cash_pct / _HUNDRED * total_equity
    required_cash = Decimal(str(mandate.min_cash_buffer_pct)) / _HUNDRED * total_equity
    cash_allowed_qty = max(existing_cash - required_cash, _ZERO) / price
    if working_qty > cash_allowed_qty:
        working_qty = cash_allowed_qty
        reasons.append(POLICY_MIN_CASH_BUFFER)

    if working_qty <= 0:
        return _denied(reasons)

    return BindingResult(quantity=working_qty, reasons=reasons, denied=False)
