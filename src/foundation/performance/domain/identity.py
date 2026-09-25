"""Accounting identity check — §3.4:
`gross_pnl - fees - slippage - funding + fx - estimated_tax = net_pnl`,
`end = start + net_pnl + Σcashflow`.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

Never raises an exception — if any input is `None` (unrealised PnL), returns
`ok=False` and reports exactly which fields are missing via `pending_fields`.
Never substitutes zero to force the identity through."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.performance.domain.models import Cashflow, CashflowKind, ComponentBreakdown

_BREAKDOWN_FIELDS = (
    "gross_pnl",
    "fees",
    "slippage",
    "funding",
    "fx",
    "estimated_tax",
    "net_pnl",
)


@dataclass(frozen=True)
class IdentityResult:
    ok: bool
    residual: Decimal | None
    pending_fields: tuple[str, ...]


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def _require(value: Decimal | None, field: str) -> Decimal:
    """Called after pending-field check: if still `None`, the caller violated
    an invariant — surface it as an exception rather than silently substituting
    zero."""
    if value is None:
        raise ValueError(f"{field}가 None (pending_fields 검사를 통과했어야 함)")
    return value


def check_identity(
    b: ComponentBreakdown,
    *,
    start_value: Decimal,
    end_value: Decimal,
    cashflows: Sequence[Cashflow],
) -> IdentityResult:
    pending = tuple(
        name for name in _BREAKDOWN_FIELDS if getattr(b, name) is None
    )
    if pending:
        return IdentityResult(ok=False, residual=None, pending_fields=pending)

    gross_pnl = _require(b.gross_pnl, "gross_pnl")
    fees = _require(b.fees, "fees")
    slippage = _require(b.slippage, "slippage")
    funding = _require(b.funding, "funding")
    fx = _require(b.fx, "fx")
    estimated_tax = _require(b.estimated_tax, "estimated_tax")
    net_pnl = _require(b.net_pnl, "net_pnl")

    computed_net = gross_pnl - fees - slippage - funding + fx - estimated_tax
    breakdown_residual = computed_net - net_pnl

    cashflow_net = sum((_signed(cf) for cf in cashflows), Decimal(0))
    expected_end = start_value + net_pnl + cashflow_net
    valuation_residual = expected_end - end_value

    ok = breakdown_residual == 0 and valuation_residual == 0
    # Sum absolute values (not a plain sum) — when two residuals differ only
    # by sign and have equal magnitude, a plain sum cancels to 0, sending the
    # contradictory signal ok=False but residual=0. Absolute sum avoids that
    # cancellation and always reflects the true mismatch magnitude.
    residual = abs(breakdown_residual) + abs(valuation_residual)
    return IdentityResult(ok=ok, residual=residual, pending_fields=())
