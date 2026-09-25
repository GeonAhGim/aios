"""L4 §2.3(C) — domain exception -> ErrorCode mapping, the `foundation.risk`
PERSONAL-mode bucket (task-2749, U-15).

Split into its own module for the same reason
`exception_registry_foundation_mandates.py` was: `exception_registry_foundation.py`
sits at the P6.line_cap(300 line) architecture guard boundary.
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.risk.application.promotion_checklist import PromotionDeniedError

EXCEPTION_MAP_FOUNDATION_PERSONAL: list[tuple[type[Exception], ErrorCode]] = [
    # U-15 -- LIVE promotion checklist unmet (fail-closed). The blocker list
    # rides in PromotionDeniedError.details["blockers"], which
    # map_exception()'s _structured_details() passes straight through to
    # the envelope's details.
    (PromotionDeniedError, ErrorCode.POLICY_LIVE_BLOCKED),
]

__all__ = ["EXCEPTION_MAP_FOUNDATION_PERSONAL"]
