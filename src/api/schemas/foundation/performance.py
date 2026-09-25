"""Performance Reporting API request/response schemas — HTTP details only;
the contract itself is wrapped in `src/foundation/performance/contracts/v1.py` (106 §2).

`scope_ref` is not accepted in the request body — PAPER scope is always the
caller's own `user_id` (P0 scope: tenant_id == user_id). Allowing clients to
send arbitrary `scope_ref` values would let them scrape another tenant's
ledger and fabricate a statement under their own name — the router fills this
on the server side."""
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
