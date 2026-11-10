"""Decision trail response schemas (J8 trail).

Mirrors ``TrailEntry`` fields; ``kind`` stays ``str``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.services.decision_trail import RiskTrailEntry as TrailEntry


class DecisionTrailEntryResponse(BaseModel):
    decision_id: UUID
    kind: str
    outcome: str
    reason_codes: list[str]
    rule_version: str
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
    summary: str


class DecisionTrailResponse(BaseModel):
    entries: list[DecisionTrailEntryResponse]


def to_decision_trail_response(
    entries: Sequence[TrailEntry],
) -> DecisionTrailResponse:
    return DecisionTrailResponse(
        entries=[
            DecisionTrailEntryResponse(
                decision_id=e.decision_id,
                kind=e.kind,
                outcome=e.outcome,
                reason_codes=list(e.reason_codes),
                rule_version=e.rule_version,
                evaluated_at=e.evaluated_at,
                expires_at=e.expires_at,
                trace_id=e.trace_id,
                summary=e.summary,
            )
            for e in entries
        ]
    )
