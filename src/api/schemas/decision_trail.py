"""Decision trail response schemas (J8 trail).

Mirrors ``TrailEntry`` fields; ``kind`` stays ``str``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.services.decision_trail import RiskTrailEntry as TrailEntry
from src.services.decision_trail import TrailEntry as MergedTrailEntry


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


class MergedTrailEntryResponse(BaseModel):
    kind: str
    occurred_at: datetime
    actor: str | None
    summary: dict[str, Any]
    evidence: dict[str, Any] | None
    unavailable: dict[str, str]


class MergedTrailResponse(BaseModel):
    entries: list[MergedTrailEntryResponse]


def to_merged_trail_response(entries: Sequence[MergedTrailEntry]) -> MergedTrailResponse:
    return MergedTrailResponse(
        entries=[
            MergedTrailEntryResponse(
                kind=e.kind,
                occurred_at=e.occurred_at,
                actor=e.actor,
                summary=e.summary,
                evidence=e.evidence,
                unavailable=e.unavailable,
            )
            for e in entries
        ]
    )
