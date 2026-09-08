"""FA-11 — positions/domain/restatement.py: retroactive fill reflection.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11
(precedes FA-10=task-2051).

`pos_journal` is append-only (§4.3 "(position_key, sequence_no) unique and
contiguous") — even a late-arriving fill (with a past `occurred_at`) can only
be appended at the tail with a new `sequence_no`. So this module does not
reorder the journal: it folds the new entry onto the existing fold as-is
(reusing LB-5 `snapshot_builder.apply_one`, no reimplementation), then wraps
the result in a pair of FA-9 `core.bitemporal.BitemporalRecord`s — the
previous "current" record closes its `tx_to` (append-only, no UPDATE), and
the new record opens `valid_from` not at `now` but at `late_entry.occurred_at`
(since that fact was always true and we're only learning it late). This
pattern is identical to FA-9's own test (the RECORD_A/RECORD_B correction
case in `tests/unit/core/test_bitemporal.py`).

Retroactive corporate-action (CORP_ACTION) handling is out of scope:
`snapshot_builder.apply_one` doesn't yet know that `entry_type` (outside
LB-5's scope, `UnsupportedEntryTypeError`) — this module doesn't filter by
`entry_type` and simply delegates, so the moment LB-5 supports CORP_ACTION,
this module supports it too with no further changes.

Pure functions only — no I/O, time is only accepted via the `now` argument.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from src.core.bitemporal import BitemporalRecord, check_no_overlap
from src.data.models.base import AssetClass
from src.foundation.positions.contracts.v1 import CostMethod, PositionJournalEntryView
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one


class NotRetroactiveError(ValueError):
    """`late_entry.occurred_at` is not earlier than `prior`'s `valid_from` —
    this isn't a correction, just the next normal entry (the caller must
    determine that before passing it in)."""


class RecordAlreadyClosedError(ValueError):
    """`prior.tx_to` is already closed — a correction can only be applied to
    the current (`tx_to=None`) record. To correct a correction, pass its
    `restated_current` back in as the new `prior`."""


def is_retroactive(late_entry: PositionJournalEntryView, current_valid_from: datetime) -> bool:
    """Whether `late_entry` reflects a fact that occurred before the current
    valid interval began."""
    return late_entry.occurred_at < current_valid_from


@dataclass(frozen=True, slots=True)
class Restatement:
    """Correction result = the closed prior record + the newly opened
    current record. Both are append-only representations (FA-A2 — neither
    is mutated in place)."""

    closed_prior: BitemporalRecord[SnapshotFold]
    restated_current: BitemporalRecord[SnapshotFold]


def restate_position(
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
    prior: BitemporalRecord[SnapshotFold],
    late_entry: PositionJournalEntryView,
    now: datetime,
) -> Restatement:
    """Folds `late_entry` (an entry already appended to the journal under
    the next `sequence_no`) onto the `prior` fold to produce the corrected
    fold, and wraps it in the FA-9 correction pattern (closed prior + new
    current)."""
    if prior.tx_to is not None:
        raise RecordAlreadyClosedError(
            f"{position_key}: prior 레코드가 이미 tx_to={prior.tx_to}로 닫혀 있습니다."
        )
    if not is_retroactive(late_entry, prior.valid_from):
        raise NotRetroactiveError(
            f"{position_key}: late_entry.occurred_at={late_entry.occurred_at}가 "
            f"prior.valid_from={prior.valid_from}보다 이르지 않습니다 — 소급이 아닙니다."
        )

    restated_fold = apply_one(
        prior.value,
        late_entry,
        position_key=position_key,
        cost_method=cost_method,
        asset_class=asset_class,
    )

    closed_prior = replace(prior, tx_to=now)
    restated_current = BitemporalRecord(
        value=restated_fold,
        valid_from=late_entry.occurred_at,
        valid_to=None,
        tx_from=now,
        tx_to=None,
    )
    check_no_overlap([closed_prior, restated_current])
    return Restatement(closed_prior=closed_prior, restated_current=restated_current)
