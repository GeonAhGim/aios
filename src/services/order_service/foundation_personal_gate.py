"""task-3986 -- personal-conservative risk bundle as PreSubmitGate's 4th
layer. Split out from `foundation_gate.py` to keep that file under the
300-line cap (P6, mirrors `foundation_compliance.py`'s own split for CM-8).

QA (task-3819) found this bundle's `evaluate_personal_order`/
`check_personal_order` (`src/foundation/risk/application/
evaluate_personal_order.py`) was never invoked by any real order path --
`src/api/routers/personal.py` is a query/config router, not a gate. This
module wires it in, scoped to a single configured account
(`PersonalOperationStatePort.personal_mode_account_id()`) so it never
affects any other MVP-1 account's existing 3-layer flow (no global
enforcement).

Known gap (docs/ops/PERSONAL_MODE.md): no live account-equity/exposure
source is wired anywhere in the codebase yet (confirmed -- the closest
candidates, `positions.NavRepository`/`SnapshotRepository`, are batch/daily
and need FX conversion glue that does not exist; `daily_report.py`'s own
docstring documents the same class of gap for realized P&L). Rather than
guess that integration, this layer takes the numeric snapshot as an
explicit, caller-supplied `OrderContext.personal_risk_snapshot` -- callers
that have not been updated to compute one (all production call sites today)
simply do not get the numeric-limit checks, while the kill-switch check
below still applies unconditionally to the scoped account.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.foundation.risk.adapters.bundle_loader import load_personal_bundle
from src.foundation.risk.application.evaluate_personal_order import check_personal_order
from src.foundation.risk.domain.rules import OrderRiskCheckInput
from src.foundation.risk.ports.notifier import PersonalNotifierPort
from src.foundation.risk.ports.state import PersonalOperationStatePort
from src.services.order_service.gate import OrderContext


@dataclass(frozen=True)
class PersonalGateResult:
    denied: bool
    reason_codes: tuple[str, ...]


_ALLOW = PersonalGateResult(False, ())


async def evaluate_personal_layer(
    context: OrderContext,
    *,
    personal_state: PersonalOperationStatePort,
    personal_notifier: PersonalNotifierPort,
) -> PersonalGateResult:
    """Only called from `foundation_gate.py`'s ALLOW path (after fence/
    control, CM-8 compliance, and mandate numeric policy all already
    passed -- see that module's docstring for the full layer order).
    Returns `_ALLOW` untouched for every account this deployment has not
    scoped personal mode to, so this layer is a true no-op for them
    regardless of what happens inside this function for the scoped
    account."""
    scoped_account_id = await personal_state.personal_mode_account_id()
    if scoped_account_id is None or scoped_account_id != context.user_id:
        return _ALLOW

    if await personal_state.is_kill_engaged():
        return PersonalGateResult(True, ("RISK_PERSONAL_KILL_SWITCH_ENGAGED",))

    if context.personal_risk_snapshot is None:
        return _ALLOW

    try:
        bundle = load_personal_bundle()
    except Exception:  # noqa: BLE001 -- fail-closed, but scoped: this can only
        # ever deny the one account personal mode is configured for (checked
        # above) -- a broken/missing config file never touches any other
        # tenant's order flow.
        return PersonalGateResult(True, ("RISK_PERSONAL_BUNDLE_UNAVAILABLE",))

    snapshot = context.personal_risk_snapshot
    order_input = OrderRiskCheckInput(
        exchange=context.exchange,
        symbol=snapshot.symbol,
        order_notional_krw=snapshot.order_notional_krw,
        account_equity_krw=snapshot.account_equity_krw,
        current_exposure_krw=snapshot.current_exposure_krw,
        daily_realized_pnl_pct=snapshot.daily_realized_pnl_pct,
    )
    # Reuses check_personal_order's own notifier/state side effects
    # (violation recording, limit-breach alert, auto-kill-engage on daily
    # loss breach) as-is -- no reimplementation.
    decision = await check_personal_order(
        bundle, order_input, notifier=personal_notifier, state=personal_state
    )
    if decision.allowed:
        return _ALLOW
    return PersonalGateResult(True, tuple(v.value for v in decision.violations))
