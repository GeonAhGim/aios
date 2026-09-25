"""Paper Execution & Control API request/response schemas — HTTP details live here;
the contract itself is wrapped by `src/foundation/paper_control/contracts/v1.py`
(Section 106 §2)."""

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
