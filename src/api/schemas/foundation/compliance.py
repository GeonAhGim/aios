"""Compliance API request/response schemas — HTTP-only shaping lives here,
wrapping the actual contract in `src/foundation/mandates/contracts/v1.py`
(doc-106 §2, same pattern as `schemas/foundation/mandates.py`).

Per the §0 audit redefinition in L4_compliance_and_regulatory_v1.0.md, this
does not create a separate `compliance` bounded context, so this router
exposes the same mandates contract types — `MandateStatusResponse` is not
redefined here, it is re-exported as-is from the mandates schema module (two
routers defining the same view under different names would drift apart
later)."""

from __future__ import annotations

from src.api.schemas.foundation.mandates import MandateStatusResponse
from src.foundation.mandates.contracts.v1 import ComplianceDecision as ComplianceDecisionView

__all__ = [
    "ComplianceDecisionView",
    "MandateStatusResponse",
]
