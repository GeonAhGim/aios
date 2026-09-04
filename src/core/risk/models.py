"""03_core_modules_v1.1.md#§3.7 — RiskCheckResult.

L4_risk_and_safety_v1.0.md#9 R-17 — `decision_id`는 R-16 evaluator가 만든
`RiskDecision.decision_id`를 그대로 실어 나르는 옵션 필드다(기본값
`None`으로 기존 호출부의 인자 없는 생성을 깨지 않는다)."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RiskCheckResult(BaseModel):
    approved: bool = False
    rejection_reason: str | None = None
    # 8.2-B 8개 지표 중 실제로 끝까지 확인한 것만(단락평가로 스킵된 나머지는 미포함)
    checked_rules: list[str] = Field(default_factory=list)
    decision_id: UUID | None = None
