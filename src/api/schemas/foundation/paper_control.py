"""Paper Execution & Control API 요청/응답 스키마 — HTTP 세부만 여기 두고,
계약 자체는 `src/foundation/paper_control/contracts/v1.py`를 감싼다
(106번 §2)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.foundation.paper_control.contracts.v1 import (
    DeploymentCommandRequest,
    DeploymentState,
    PaperDeploymentView,
    RequestDeploymentRequest,
)

__all__ = [
    "DeploymentCommandRequest",
    "DeploymentListResponse",
    "DeploymentState",
    "PaperDeploymentView",
    "RequestDeploymentRequest",
]


class DeploymentListResponse(BaseModel):
    deployments: list[PaperDeploymentView]
    as_of: datetime
