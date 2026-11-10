"""Decision trail response schemas (J8 trail).

Wraps ``TrailEntry`` fields into API response shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel

from src.services.decision_trail import TrailEntry as _TrailEntry


class DecisionTrailEntryResponse(BaseModel):
    decision_id: UUID
    kind: str
    outcome: str
    reason_codes: list[str]
    rule_version: str
    evaluated_at: str
    expires_at: str | None
    trace_id: UUID | None
    summary: str


class DecisionTrailResponse(BaseModel):
    entries: list[DecisionTrailEntryResponse]


def to_decision_trail_response(
    entries: Sequence[_TrailEntry],
) -> DecisionTrailResponse:
    return DecisionTrailResponse(
        entries=[
            DecisionTrailEntryResponse(
                decision_id=e.summary.get("decision_id", UUID(int=0)),
                kind=e.kind.value,
                outcome=e.summary.get("outcome", ""),
                reason_codes=list(e.summary.get("reason_codes", [])),
                rule_version=e.summary.get("rule_version", ""),
                evaluated_at=str(e.summary.get("evaluated_at", "")),
                expires_at=str(e.summary.get("expires_at"))
                if e.summary.get("expires_at")
                else None,
                trace_id=e.summary.get("trace_id"),
                summary=e.summary.get("summary", ""),
            )
            for e in entries
        ]
    )
