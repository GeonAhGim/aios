"""FA-8 - allocation/application/allocate_fills.py: fills -> sub_account
allocation + ledger linkage.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-8
(table in §9, allocation row in §2.1).

This use case adds only I/O orchestration ("look up fills -> compute
allocation -> post a ledger entry per sub_account -> persist the allocation
record") on top of FA-7's pure domain (`domain/policy.allocate`,
`domain/average_price.blended_average_price`/`apply_average_price`) - the
allocation policy and weighted-average math themselves are not
reimplemented here (PM decision, task-1796).

Ledger linkage reuses LC-4 `posting_rules`/LC-9 `post_entry` as-is. No new
event type is added to the 9 `posting_rules` already knows; instead each
sub_account allocation gets its own entry via the existing
`MANUAL_ADJUSTMENT` event (an arbitrary debit/credit account pair + a
single amount). Expressing an N-way allocation as one entry would need a
new LC-4 event type, which is exactly what "no new posting rules" forbids
- so this repeats a 2-line entry once per sub_account instead. Each entry
is independently balanced (debit == credit), so it satisfies
`balance_rules.check_balanced` (inside post_entry) unchanged.

The account code is built from `SubAccount.owner_ref` (FK to
`users.user_id`, FA-2 migration - so it lives in the same identifier space
as the existing `USER:*` wallet accounts): `USER:{owner_ref}:AVAILABLE`,
the real owner's wallet. The other leg is the existing
`PLATFORM:CASH_CLEARING` (LC-2, already used by topup/purchase_flow): a
buy flows from the wallet to the clearing account, a sell flows the other
way (the debit/credit sign convention in §4.4 - assets/expenses increase
on debit, liabilities/revenue increase on credit - is decided by
`chart_of_accounts.account_type`, not reimplemented here). If the
`ledger_account`/`ledger_balance` rows for `owner_ref` do not exist yet,
they are created once, following the same pattern as
`topup.py::_reconcile_ledger_with_projection` (account provisioning is
outside posting_rules/post_entry's contract - LC-9's contract is "reject
an unknown account fail-closed", so this file owns that responsibility).

Idempotency: each sub_account's entry pins `event_ref` to
`f"fill_allocation:{order_id}:{sub_account_id}"` so LC-3 (the
`idempotency_key` `post_entry` uses) absorbs retries as-is. PLT-14 (I-03's
four-fold scope) is the `Idempotency-Key` header scope
(route+tenant_id+subject_id+header_key) for HTTP POST entry points; that
scope does not apply to this internal orchestration function (not an HTTP
endpoint) - instead the same "deterministic key + conditional insert"
principle is applied with this function's own natural key
(order_id, sub_account_id). `fill_allocation`'s
`UNIQUE(order_id, sub_account_id)` is likewise not a bespoke dedup table
but an integrity constraint on the allocation fact itself (see the
migration's docstring).

FA-A3 (allocation total == fill quantity, error <= 1 minimum unit) is
already enforced by `policy.allocate`/`average_price.apply_average_price`,
so this file does not re-check it. The entry amount (`LedgerEvent.amount`)
is the raw notional (quantity * average_price, lossless) rounded once to
0.01 (reusing `policy.round_to_quantum`) to satisfy `PostingLine.amount`'s
contract (`decimal_places=2`) and `ledger_posting_line.amount
NUMERIC(20,2)` (LC-1 §3.3) - `fill_allocation.quantity`/`average_price`
keep the original precision (NUMERIC(30,10)) that never reaches the
ledger.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.allocation.domain.average_price import (
    PartialFill,
    SubAccountAllocation,
    apply_average_price,
    blended_average_price,
)
from src.foundation.allocation.domain.policy import (
    AllocationPolicy,
    ManualTarget,
    WeightTarget,
    allocate,
    round_to_quantum,
)
from src.foundation.entities.application.resolve_context import EntityRepository
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.foundation.ledger.contracts.v1 import UserSub as LedgerUserSub
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

_NOTIONAL_QUANTUM = Decimal("0.01")


class NoFillsError(ValueError):
    """`order_id` has no fills, so there is nothing to allocate."""


class AllocationTargetError(ValueError):
    """The target sub_account/portfolio/fund is missing or closed (fail-closed)."""


@dataclass(frozen=True)
class FillAllocationResult:
    """Allocation + ledger entry result for one sub_account."""

    sub_account_id: UUID
    quantity: Decimal
    average_price: Decimal
    fund_id: UUID
    portfolio_id: UUID
    ledger_entry_id: UUID
    replayed: bool


@dataclass(frozen=True)
class _ResolvedTarget:
    owner_ref: UUID
    portfolio_id: UUID
    fund_id: UUID
    base_currency: Currency


async def _resolve_target(
    entities: EntityRepository, tenant_id: UUID, sub_account_id: UUID
) -> _ResolvedTarget:
    """`sub_account_id` -> (owner_ref, portfolio_id, fund_id, base_currency).
    If any level of the hierarchy is missing or closed, reject rather than
    guessing (same fail-closed principle as FA-5's `resolve_context`)."""
    sub_account = await entities.get_sub_account(tenant_id, sub_account_id)
    if sub_account is None or sub_account.closed_at is not None:
        raise AllocationTargetError(f"sub_account {sub_account_id} missing or closed")
    portfolio = await entities.get_portfolio(tenant_id, sub_account.portfolio_id)
    if portfolio is None or portfolio.closed_at is not None:
        raise AllocationTargetError(f"portfolio {sub_account.portfolio_id} missing or closed")
    fund = await entities.get_fund(tenant_id, portfolio.fund_id)
    if fund is None or fund.closed_at is not None:
        raise AllocationTargetError(f"fund {portfolio.fund_id} missing or closed")
    return _ResolvedTarget(
        owner_ref=sub_account.owner_ref,
        portfolio_id=portfolio.portfolio_id,
        fund_id=fund.fund_id,
        base_currency=fund.base_currency,
    )


async def _ensure_user_account(conn: asyncpg.Connection, code: str, currency: Currency) -> None:
    """Same provisioning pattern as
    `topup.py::_reconcile_ledger_with_projection` - create the
    `ledger_account`/`ledger_balance` rows if they do not exist yet (LC-9
    never creates an unknown account silently, so this file carries that
    responsibility for its caller)."""
    negative_ok = allows_negative(code)
    await conn.execute(
        "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (account_code) DO NOTHING",
        code, account_type(code).value, currency.value, negative_ok,
    )
    await conn.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "
        "ON CONFLICT (account_id) DO NOTHING",
        code, negative_ok,
    )


async def _post_allocation_entry(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    order_id: UUID,
    side: str,
    allocation: SubAccountAllocation,
    entities: EntityRepository,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    trace_id: UUID,
    actor_subject_id: UUID | None,
) -> FillAllocationResult:
    target = await _resolve_target(entities, tenant_id, allocation.sub_account_id)
    fund_id, portfolio_id, currency = target.fund_id, target.portfolio_id, target.base_currency

    code = ua(target.owner_ref, LedgerUserSub.AVAILABLE)
    await _ensure_user_account(conn, code, currency)

    notional = round_to_quantum(allocation.quantity * allocation.average_price, _NOTIONAL_QUANTUM)
    debit_account, credit_account = (
        (code, PLATFORM_CASH_CLEARING) if side == "BUY" else (PLATFORM_CASH_CLEARING, code)
    )

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"fill_allocation:{order_id}:{allocation.sub_account_id}",
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        trace_id=trace_id,
        amount=notional,
        currency=currency,
        parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
        fund_id=fund_id,
        portfolio_id=portfolio_id,
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )

    await conn.execute(
        "INSERT INTO fill_allocation "
        "(order_id, sub_account_id, tenant_id, fund_id, portfolio_id, quantity, "
        " average_price, ledger_entry_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (order_id, sub_account_id) DO NOTHING",
        order_id,
        allocation.sub_account_id,
        tenant_id,
        fund_id,
        portfolio_id,
        allocation.quantity,
        allocation.average_price,
        entry.entry_id,
    )

    return FillAllocationResult(
        sub_account_id=allocation.sub_account_id,
        quantity=allocation.quantity,
        average_price=allocation.average_price,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        ledger_entry_id=entry.entry_id,
        replayed=entry.replayed,
    )


async def allocate_order_fills(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    order_id: UUID,
    policy: AllocationPolicy,
    weight_targets: Sequence[WeightTarget] = (),
    manual_targets: Sequence[ManualTarget] = (),
    quantum: Decimal,
    price_quantum: Decimal,
    entities: EntityRepository,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    trace_id: UUID,
    actor_subject_id: UUID | None = None,
) -> tuple[FillAllocationResult, ...]:
    """Look up every fill for `order_id`, split the quantity across
    sub_accounts with `policy`, assign the blended average price across
    the partial fills, then post a ledger entry + `fill_allocation` record
    per sub_account. Reuses the caller's already-open `conn`/transaction
    (same contract as post_entry - this function does not decide
    commit/rollback)."""
    fill_rows = await conn.fetch(
        "SELECT quantity, price FROM fills WHERE order_id = $1", order_id
    )
    if not fill_rows:
        raise NoFillsError(f"order_id={order_id}: no fills to allocate")
    side = await conn.fetchval("SELECT side FROM orders WHERE order_id = $1", order_id)
    if side is None:
        raise NoFillsError(f"order_id={order_id}: order not found")

    partial_fills = [PartialFill(quantity=r["quantity"], price=r["price"]) for r in fill_rows]
    total_quantity = sum((f.quantity for f in partial_fills), Decimal("0"))
    total_notional = sum((f.quantity * f.price for f in partial_fills), Decimal("0"))

    lines = allocate(
        policy,
        total_quantity,
        weight_targets=weight_targets,
        manual_targets=manual_targets,
        quantum=quantum,
    )
    average_price = blended_average_price(partial_fills, price_quantum)
    allocations = apply_average_price(
        lines,
        average_price,
        total_notional=total_notional,
        notional_quantum=_NOTIONAL_QUANTUM,
    )

    results = []
    for allocation in allocations:
        result = await _post_allocation_entry(
            conn,
            tenant_id=tenant_id,
            order_id=order_id,
            side=side,
            allocation=allocation,
            entities=entities,
            journal=journal,
            balances=balances,
            audit=audit,
            clock=clock,
            trace_id=trace_id,
            actor_subject_id=actor_subject_id,
        )
        results.append(result)
    return tuple(results)
