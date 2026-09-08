"""FA-14 — positions projection: replay `pos_journal`(LB-5) into position state.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4, §9 FA-14
("define a projection using the existing pos_journal(LB-5) as the event source").

This projection does not recompute anything new — `snapshot_builder.fold`
(LB-5) already *is* "snapshot = fold(journal)"(§4.3) as a pure function, and
`rebuild_snapshot`(LB-13) already runs this exact replay in production. What
FA-14 asks for — a pure projection from the event source to state — is that
same function, re-exposed under the eventstore projections namespace instead
of re-implemented (task-1703/decision precedent: reuse, don't rebuild).
"""
from __future__ import annotations

from src.foundation.positions.domain.snapshot_builder import (
    SnapshotFold,
    UnsupportedEntryTypeError,
    apply_one,
)
from src.foundation.positions.domain.snapshot_builder import (
    fold as project,
)

__all__ = ["SnapshotFold", "UnsupportedEntryTypeError", "apply_one", "project"]
