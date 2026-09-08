"""FA-14 — ledger projection: replay `ledger_journal_entry`+`ledger_posting_line`
(LC) into per-account balance state.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4, §9 FA-14
("define a projection using the existing ledger_journal_entry(LC) as the event source").

Pure fold, no I/O. Two pieces are reused rather than re-implemented (decision:
new event tables/logic are out of scope, this is a context extension):

- `balance_rules.apply`(LC-3) accumulates a delta onto a `Balance` and
  re-checks the §4.4 invariants (`held >= 0`, `available >= 0` unless
  `allow_negative`) — replay re-validates the same fail-closed conditions
  that held at write time.
- The debit/credit sign rule (§4.4 "assets and expenses increase on the
  debit side, liabilities and revenue increase on the credit side") is the
  same one-line rule `post_entry._signed_delta`(LC-9) applies —
  that function is module-private, so this module re-derives the same
  judgement from the public `chart_of_accounts.account_type`(LC-2) rather
  than reaching into another module's private name. No new sign table is
  invented; §4.4 defines exactly one.

`ledger_balance.last_entry_seq` is a per-account touch counter, not a copy
of the global `ledger_journal_entry.sequence_no`(`postgres_balance_repository.
apply` docstring) — this fold advances it the same way LC-9 does: +1 per
entry that touches the account, once per entry even if the entry has
multiple lines on that account (mirrors `post_entry.post_entry`'s
per-account `deltas` aggregation).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import AccountType, JournalEntryView, PostingLine, Side
from src.foundation.ledger.domain import balance_rules
from src.foundation.ledger.domain.balance_rules import Balance
from src.foundation.ledger.domain.chart_of_accounts import account_type, allows_negative

_DEBIT_NORMAL_TYPES = frozenset({AccountType.ASSET, AccountType.EXPENSE})

LedgerProjection = Mapping[str, Balance]


def _signed_delta(line: PostingLine) -> Decimal:
    debit_increases = account_type(line.account_code) in _DEBIT_NORMAL_TYPES
    increases = (line.side is Side.DEBIT) == debit_increases
    return line.amount if increases else -line.amount


def _seed(account_code: str, currency: Currency) -> Balance:
    return Balance(
        account_code=account_code,
        balance=Decimal("0"),
        held=Decimal("0"),
        currency=currency,
        allow_negative=allows_negative(account_code),
        last_entry_seq=0,
    )


def apply_entry(state: LedgerProjection, entry: JournalEntryView) -> dict[str, Balance]:
    """Fold one journal entry (all of its lines) into `state`."""
    next_state = dict(state)
    deltas: dict[str, Decimal] = {}
    currencies: dict[str, Currency] = {}
    for line in entry.lines:
        deltas[line.account_code] = deltas.get(line.account_code, Decimal("0")) + _signed_delta(
            line
        )
        currencies.setdefault(line.account_code, line.currency)

    for code, delta in deltas.items():
        current = next_state.get(code) or _seed(code, currencies[code])
        next_state[code] = balance_rules.apply(
            current,
            delta_balance=delta,
            delta_held=Decimal("0"),
            entry_seq=current.last_entry_seq + 1,
        )
    return next_state


def project(entries: Sequence[JournalEntryView]) -> dict[str, Balance]:
    """Fold `entries`(sequence_no ascending) from the start."""
    state: dict[str, Balance] = {}
    for entry in entries:
        state = apply_entry(state, entry)
    return state
