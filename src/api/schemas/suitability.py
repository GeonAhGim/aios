"""Task-15 — Suitability evaluation API response schemas.

Deviation: 16_backend_signatures.md §16.5 Draft's RiskAssessmentRequest
field names (investment_experience_years, capital_at_risk_pct, etc.) differ from the
actually implemented SuitabilityQuestionnaire/SuitabilityAnswers
(services/suitability_questionnaire.py) in name and value domain — the real service
contract (SuitabilityAnswers), already completed and tested, is reused as-is for
the request body.

The Draft sketched returning each history item as RiskProfileResponse (including
next_reassessment_due), but "next reassessment due" has no meaning for past
history entries (that concept applies only to the current rating) — history
responses are honestly separated into a distinct model carrying only the actual
values (risk_profile, answers, assessed_at).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from src.services.risk_profile_service import RiskProfileRecord


class RiskProfileResponse(BaseModel):
    risk_profile: str
    assessed_at: datetime
    next_reassessment_due: datetime
    is_higher_risk_than_previous: bool = False


def to_risk_profile_response(record: RiskProfileRecord) -> RiskProfileResponse:
    return RiskProfileResponse(
        risk_profile=record.risk_profile,
        assessed_at=record.assessed_at,
        next_reassessment_due=record.next_reassessment_due,
        is_higher_risk_than_previous=record.is_higher_risk_than_previous,
    )


class RiskProfileHistoryEntry(BaseModel):
    risk_profile: str
    assessed_at: datetime
    answers: dict[str, Any]


def to_history_entry(row: dict[str, Any]) -> RiskProfileHistoryEntry:
    answers = row["assessment_answers"]
    return RiskProfileHistoryEntry(
        risk_profile=row["risk_profile"],
        assessed_at=row["assessed_at"],
        answers=json.loads(answers) if isinstance(answers, str) else answers,
    )
