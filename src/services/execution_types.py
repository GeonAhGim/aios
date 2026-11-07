"""FD-16 execution service shared types — contains only exception and summary models
shared between execution_service.py and execution_control.py (split to stay within
P6 300-line cap; prevents circular import — execution_control.py does not import
execution_service.py)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ExecutionCreateError(Exception):
    """FD-16.1/16.2 failure — router converts to 400/403/404."""


class ExecutionControlError(Exception):
    """FD-16.3 failure — start/pause/stop rejected. Router converts to 400/403/404."""


class ExecutionEndpointRetiredError(ExecutionControlError):
    """폐기된 전환 경로 호출 -- 라우터가 410(Gone)으로 변환한다(J8: live-conversion으로 대체)."""


class ExecutionSummary(BaseModel):
    id: int
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    approval_request_id: int | None = None
    max_drawdown_pct: Decimal | None = None
