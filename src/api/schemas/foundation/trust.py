"""Trust Core API request/response schemas — keep only HTTP details (path parameters,
etc.) here; the contract itself wraps `src/foundation/trust/contracts/v1.py` (item 106 §2)."""
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
    """Item 73 §5 GET /v1/trust/status — TrustStatusView projection."""

    tenant_id: UUID
    consents: list[ConsentDecision]
    as_of: datetime
