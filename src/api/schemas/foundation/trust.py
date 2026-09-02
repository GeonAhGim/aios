"""Trust Core API 요청/응답 스키마 — HTTP 세부(경로 파라미터 등)만 여기 두고,
계약 자체는 `src/foundation/trust/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState, TrustFreshnessDecision

__all__ = ["ConsentDecision", "ConsentState", "TrustFreshnessDecision", "AcceptDisclosureRequest"]


class AcceptDisclosureRequest(BaseModel):
    purpose: str
    disclosure_revision: int


class TrustStatusResponse(BaseModel):
    """73번 §5 GET /v1/trust/status — TrustStatusView 프로젝션."""

    tenant_id: UUID
    consents: list[ConsentDecision]
    as_of: datetime
