"""Portfolio Mandate API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약 자체는
`src/foundation/mandates/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from src.foundation.mandates.contracts.v1 import (
    MandateRevisionView,
    MandateRuleInput,
    PolicyDecisionView,
    PolicyEvaluationSubject,
)

__all__ = [
    "ActivateRevisionRequest",
    "MandateRevisionView",
    "MandateRuleInput",
    "MandateStatusResponse",
    "PolicyDecisionView",
    "PolicyEvaluationSubject",
]


class ActivateRevisionRequest(BaseModel):
    """password/totp_code가 있으면 라우터가 reauthenticate()를 호출해 material
    change 게이트(activate_revision.py)를 통과시킨다 — 없으면
    reauthenticated=False로 넘겨 non-material 변경만 허용한다."""

    password: str | None = None
    totp_code: str | None = None


class MandateStatusResponse(BaseModel):
    tenant_id: UUID
    active_revision: MandateRevisionView | None
    pending_revision: MandateRevisionView | None
