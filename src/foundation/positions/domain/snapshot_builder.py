"""LB-5 — rule (snapshot_builder) that folds the journal entry stream into `pos_snapshot`.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9 LB-5.

Implements §4.3 "snapshot = fold(journal)" literally: [[apply_one]] folds one
entry into the accumulated state (`SnapshotFold`), and [[fold]] is nothing more
than `functools.reduce(apply_one, ...)` — no separate optimized path exists.
Since rebuild (`rebuild_snapshot`, LB-13) must be able to reconstruct the
snapshot from the journal alone, cost-basis (FIFO/WEIGHTED) lots are also
recomputed on every `FILL` entry via [[cost_basis.selector.cost_basis_for]]
(delegated to LB-3, no duplicate implementation) — the `realized_pnl_base`
already stored on the entry is trusted and accumulated as-is; reapplying the
cost method is used only to update lots/quantity.

The `fees_base` accrual rule is shared with [[journal_rules]]: if `entry.fee`
is present, add `entry.fee.amount * (entry.fx_rate or 1)` — applied
unconditionally regardless of entry_type (fees attached to a fill and standalone
`FEE` entries both use the same column). `funding_base` is accrued only from
the `realized_pnl_base` column of `FUNDING` entries (reused; see
[[journal_rules.funding_entry]]).

Pure domain (zero DB/HTTP imports).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import reduce

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    JournalEntryType,
    Lot,
    PositionJournalEntryView,
)
from src.foundation.positions.domain.cost_basis.fifo import FifoLots, FillEvent
from src.foundation.positions.domain.cost_basis.selector import CostBasis, cost_basis_for
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage
from src.foundation.positions.domain.journal_rules import validate_sequence


class UnsupportedEntryTypeError(ValueError):
    """`entry_type` that `apply_one` does not yet know how to fold
    (`ADJUSTMENT`, `CORP_ACTION` — out of scope for this leaf). Following the
    same precedent as [[cost_basis.selector.UnknownAssetClassError]], this
    surfaces as an exception instead of a silent skip — there is no `POS_*`
    code that maps exactly to the LB-1 taxonomy."""

    def __init__(self, position_key: str, entry_type: JournalEntryType) -> None:
        super().__init__(f"{position_key}: 지원하지 않는 entry_type입니다: {entry_type.value}")
        self.position_key = position_key
        self.entry_type = entry_type


@dataclass(frozen=True, slots=True)
class SnapshotFold:
    """The foldable subset of `pos_snapshot` (only the fields listed in the §9
    LB-5 DoD — static account context such as
    `tenant_id`/`account_id`/`instrument_id`/`base_currency`/`updated_at` is
    absent from this type since it cannot be derived from the journal alone.
    The caller builds the full `PositionSnapshotView` by layering that context
    onto this result)."""

    quantity: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")
    lots: tuple[Lot, ...] = ()
    realized_pnl_base: Decimal = Decimal("0")
    fees_base: Decimal = Decimal("0")
    funding_base: Decimal = Decimal("0")
    last_journal_seq: int = 0


def _seeded_cost_basis(
    cost_method: CostMethod, asset_class: AssetClass, lots: tuple[Lot, ...]
) -> CostBasis:
    """Picks FIFO vs WEIGHTED via [[cost_basis.selector.cost_basis_for]]
    (WEIGHTED is forced when asset_class is a derivative — delegated, no
    duplicate implementation), and seeds the existing `lots` here since the
    selector always returns an empty instance."""
    template = cost_basis_for(cost_method, asset_class)
    if isinstance(template, FifoLots):
        return FifoLots(lots)
    assert isinstance(template, WeightedAverage)
    return WeightedAverage(lots[0] if lots else None)


def _avg_cost(quantity: Decimal, lots: tuple[Lot, ...]) -> Decimal:
    if quantity <= 0:
        return Decimal("0")
    total_cost = sum((lot.quantity * lot.unit_cost for lot in lots), Decimal("0"))
    return total_cost / quantity


def apply_one(
    state: SnapshotFold,
    entry: PositionJournalEntryView,
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
) -> SnapshotFold:
    """Folds one entry into `state`. §4.3 "(position_key, sequence_no) unique,
    contiguous" — an out-of-order or skipped entry is rejected with
    `SequenceConflictError` (reuses `journal_rules.validate_sequence`)."""
    validate_sequence(position_key, state.last_journal_seq, entry.sequence_no)

    quantity = state.quantity
    lots = state.lots
    realized_pnl_base = state.realized_pnl_base
    funding_base = state.funding_base

    if entry.entry_type is JournalEntryType.FILL:
        if entry.price is None:
            raise ValueError(
                f"{position_key}: FILL 엔트리는 price가 필요합니다(seq={entry.sequence_no})."
            )
        basis = _seeded_cost_basis(cost_method, asset_class, lots)
        fill = FillEvent(
            side=OrderSide.BUY if entry.qty_delta > 0 else OrderSide.SELL,
            quantity=abs(entry.qty_delta),
            price=entry.price.amount,
            occurred_at=entry.occurred_at,
        )
        result = basis.apply(fill)
        lots = result.lots
        quantity = sum((lot.quantity for lot in lots), Decimal("0"))
        realized_pnl_base = realized_pnl_base + entry.realized_pnl_base
    elif entry.entry_type is JournalEntryType.FUNDING:
        funding_base = funding_base + entry.realized_pnl_base
    elif entry.entry_type is JournalEntryType.FEE:
        pass
    else:
        raise UnsupportedEntryTypeError(position_key, entry.entry_type)

    fees_base = state.fees_base
    if entry.fee is not None:
        multiplier = entry.fx_rate if entry.fx_rate is not None else Decimal("1")
        fees_base = fees_base + entry.fee.amount * multiplier

    return SnapshotFold(
        quantity=quantity,
        avg_cost=_avg_cost(quantity, lots),
        lots=lots,
        realized_pnl_base=realized_pnl_base,
        fees_base=fees_base,
        funding_base=funding_base,
        last_journal_seq=entry.sequence_no,
    )


def fold(
    entries: Sequence[PositionJournalEntryView],
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
    initial: SnapshotFold | None = None,
) -> SnapshotFold:
    """Folds `entries` (ascending sequence_no) from the start —
    literally `functools.reduce(apply_one, entries, initial)` (determinism
    and associativity follow automatically since `apply_one` is a pure
    function)."""
    start = initial if initial is not None else SnapshotFold()
    return reduce(
        lambda acc, entry: apply_one(
            acc, entry, position_key=position_key, cost_method=cost_method, asset_class=asset_class
        ),
        entries,
        start,
    )
