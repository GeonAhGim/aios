"""Connected Asset API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약 자체는
`src/foundation/connections/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.foundation.connections.contracts.v1 import (
    AccountConnectionView,
    AccountSnapshotView,
    BeginConnectionRequest,
    CapabilityScope,
    ConnectionState,
)

__all__ = [
    "AccountConnectionView",
    "AccountSnapshotView",
    "BeginConnectionRequest",
    "CapabilityScope",
    "ConnectionListResponse",
    "ConnectionState",
]


class ConnectionListResponse(BaseModel):
    connections: list[AccountConnectionView]
    as_of: datetime
