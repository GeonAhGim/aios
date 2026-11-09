"""Decision trail entry model (J8 trail).

Each row in the trail represents a single risk / execution decision
recorded during an execution's lifetime.  This dataclass is a
lightweight, serialisable view — the authoritative schema lives in
``src/core/risk/decision.py`` (``RiskDecision``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class TrailEntry:
    """One decision recorded in the trail.

    Fields mirror ``RiskDecision`` but ``kind`` is a plain ``str``
    (``GateKind`` value) so the API response schema stays simple.
    """

    decision_id: UUID
    kind: str  # GateKind value string
    outcome: str  # RiskOutcome value string
    reason_codes: tuple[str, ...]
    rule_version: str
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
    # Human-readable summary derived from rule_results / obligations.
    summary: str
