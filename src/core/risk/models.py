"""03_core_modules_v1.1.md#§3.7 — RiskCheckResult."""
from __future__ import annotations

from pydantic import BaseModel


class RiskCheckResult(BaseModel):
    approved: bool
    rejection_reason: str | None = None
    # 8.2-B 8개 지표 중 실제로 끝까지 확인한 것만(단락평가로 스킵된 나머지는 미포함)
    checked_rules: list[str]
