"""FD-16 실행 서비스 공용 타입 — execution_service.py/execution_control.py가
공유하는 예외·요약 모델만 담는다(P6 300줄 상한 준수로 분리, 순환 import
방지 — execution_control.py가 execution_service.py를 import하지 않는다)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ExecutionCreateError(Exception):
    """FD-16.1/16.2 실패 — 라우터가 400/403/404로 변환."""


class ExecutionControlError(Exception):
    """FD-16.3 실패 — 시작/일시정지/중지 거부. 라우터가 400/403/404로 변환."""


class ExecutionSummary(BaseModel):
    id: int
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    approval_request_id: int | None = None
    max_drawdown_pct: Decimal | None = None
