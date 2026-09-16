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
    # U-15 — LIVE 전환 체크리스트 미충족(fail-closed). 사유 목록은
    # PromotionDeniedError.details["blockers"]에 실려 map_exception()의
    # _structured_details()가 그대로 봉투 details로 전달한다.
    (PromotionDeniedError, ErrorCode.POLICY_LIVE_BLOCKED),
]

__all__ = ["EXCEPTION_MAP_FOUNDATION_PERSONAL"]
