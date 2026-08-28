"""15번 — 적합성평가 API 응답 스키마.

편차: 16_backend_signatures.md §16.5 Draft의 RiskAssessmentRequest
필드명(investment_experience_years, capital_at_risk_pct 등)은 실제 구현된
SuitabilityQuestionnaire/SuitabilityAnswers(services/suitability_questionnaire.py)
와 이름·값 도메인이 다르다 — 이미 완성돼 테스트까지 통과한 실제 서비스
계약(SuitabilityAnswers)을 요청 바디로 그대로 재사용한다.

Draft는 이력 각 항목도 RiskProfileResponse(next_reassessment_due 포함)로
반환하도록 스케치했지만, 과거 이력행에 "다음 재평가 예정일"은 의미가
없다(그 개념은 현재 등급에만 적용) — 이력 응답은 실제 값(risk_profile,
answers, assessed_at)만 담는 별도 모델로 정직하게 분리한다.
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
