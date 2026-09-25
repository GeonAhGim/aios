"""L4 §2.3(C) — domain exception -> ErrorCode mapping, the
`foundation.mandates.*` bucket.

`exception_registry_foundation.py` hit the P6.line_cap architecture guard
(300 lines) again (task-2618), for the same reason it was already split
once before — so the mandates cluster is carved out again here. The public
API is unchanged: `exception_registry_foundation.py` appends this module's
`EXCEPTION_MAP_FOUNDATION_MANDATES` onto its own
`EXCEPTION_MAP_FOUNDATION` (every other module still imports only from
`exception_registry_foundation`/`exception_mapping`).
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.mandates.application.activate_revision import (
    CoolingOffNotElapsedError,
    CrossTenantMandateAccessError,
    InvalidRevisionStateError,
    MaterialChangeRequiresFreshConsentError,
    MaterialChangeRequiresReauthError,
    RevisionNotFoundError,
    SelfApprovalNotAllowedError,
)
from src.foundation.mandates.application.create_draft_mandate import MandateAlreadyExistsError
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.explain import (
    ExplainBundleDriftedError,
    ExplainDecisionNotFoundError,
)

EXCEPTION_MAP_FOUNDATION_MANDATES: list[tuple[type[Exception], ErrorCode]] = [
    (MandateAlreadyExistsError, ErrorCode.STATE_INVALID_TRANSITION),
    (NoActiveMandateError, ErrorCode.RESOURCE_NOT_FOUND),
    (RevisionNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantMandateAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    # CM-17 (task-2618) explain() API wiring -- folds a nonexistent decision
    # and one owned by another tenant into the same 404 (§9 CM-17 DoD
    # "404 isomorphism").
    (ExplainDecisionNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (ExplainBundleDriftedError, ErrorCode.STATE_INVALID_TRANSITION),
    (InvalidRevisionStateError, ErrorCode.STATE_INVALID_TRANSITION),
    (MaterialChangeRequiresReauthError, ErrorCode.AUTH_MFA_REQUIRED),
    (MaterialChangeRequiresFreshConsentError, ErrorCode.POLICY_DENIED),
    (CoolingOffNotElapsedError, ErrorCode.STATE_INVALID_TRANSITION),
    # CM-5 (task-2118) — author != approver (CM-A3) violation, mapped to 400 (per DoD).
    (SelfApprovalNotAllowedError, ErrorCode.VALIDATION_INVALID_FIELD),
]

__all__ = ["EXCEPTION_MAP_FOUNDATION_MANDATES"]
