"""Reconciliation & Resilience API 요청/응답 스키마 — HTTP 세부만 여기 두고,
계약 자체는 `src/foundation/reconciliation/contracts/v1.py`를 감싼다
(106번 §2)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationItemView,
    ReconciliationRunView,
    ReconciliationStateView,
    ResolveReconciliationRequest,
    RunReconciliationRequest,
)

__all__ = [
    "Classification",
    "EntitySnapshot",
    "ReconciliationItemView",
    "ReconciliationRunView",
    "ReconciliationStateListResponse",
    "ReconciliationStateView",
    "ResolveReconciliationRequest",
    "RunReconciliationRequest",
]


class ReconciliationStateListResponse(BaseModel):
    states: list[ReconciliationStateView]
    as_of: datetime
