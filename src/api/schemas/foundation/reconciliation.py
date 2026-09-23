"""Reconciliation & Resilience API request/response schemas — HTTP details only here;
the contract itself lives in `src/foundation/reconciliation/contracts/v1.py`
(Standard-106 §2)."""
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
