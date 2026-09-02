"""Performance Reporting API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약
자체는 `src/foundation/performance/contracts/v1.py`를 감싼다(106번 §2).

`scope_ref`는 요청 본문에서 받지 않는다 — PAPER 스코프는 항상 호출자
자신의 `user_id`다(P0 스콥 tenant_id == user_id). 클라이언트가 임의
`scope_ref`를 보낼 수 있게 하면 다른 tenant의 원장을 긁어 자기 이름으로
statement를 만들 수 있는 경로가 생긴다 — 라우터가 서버 쪽에서 채운다."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.foundation.performance.contracts.v1 import PerformanceStatementView, StatementScope

__all__ = [
    "ComputeStatementRequest",
    "CorrectStatementRequest",
    "PerformanceStatementListResponse",
    "PerformanceStatementView",
    "StatementScope",
]


class ComputeStatementRequest(BaseModel):
    scope: StatementScope
    period_start: datetime
    period_end: datetime
    methodology_version: str | None = None


class CorrectStatementRequest(BaseModel):
    reason: str


class PerformanceStatementListResponse(BaseModel):
    statements: list[PerformanceStatementView]
