"""18번 — 관리자 도구 API 요청 바디 + 목록 응답 스키마."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class UserStatusChangeRequest(BaseModel):
    status: str


class SuspendSellerRequest(BaseModel):
    reason: str


class DisputeResolveRequest(BaseModel):
    decision: str
    reason: str


class DisputeSummary(BaseModel):
    id: int
    purchase_id: int
    submitted_by: UUID
    reason: str
    status: str
    resolution_decision: str | None
    resolution_reason: str | None
    resolved_by: UUID | None
    created_at: datetime
    resolved_at: datetime | None


def to_dispute_summary(row: dict[str, Any]) -> DisputeSummary:
    return DisputeSummary(**row)
