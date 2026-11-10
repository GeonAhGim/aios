"""Decision trail response schemas (J8 trail).

Carries ``TrailEntry`` (src/services/decision_trail.py) to the API shape without losing data.
Conversion, expansion and risk-decision entries have different summary keys, so flattening
them into one record drops values: ``summary`` and ``evidence`` stay objects, and values that
are missing are listed in ``unavailable`` with a reason instead of being filled with zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.services.decision_trail import TrailEntry


class DecisionTrailEntryResponse(BaseModel):
    kind: str
    occurred_at: datetime
    actor: str | None
    summary: dict[str, Any]
    evidence: dict[str, Any] | None
    unavailable: dict[str, str] = Field(default_factory=dict)


class DecisionTrailResponse(BaseModel):
    entries: list[DecisionTrailEntryResponse]


def to_decision_trail_response(entries: Sequence[TrailEntry]) -> DecisionTrailResponse:
    return DecisionTrailResponse(
        entries=[
            DecisionTrailEntryResponse(
                kind=e.kind.value,
                occurred_at=e.occurred_at,
                actor=e.actor,
                summary=dict(e.summary),
                evidence=dict(e.evidence) if e.evidence is not None else None,
                unavailable=dict(e.unavailable),
            )
            for e in entries
        ]
    )
