"""20번 — 기간별 보고서 API 라우터 (FD-20.1).

Spec: 기능설계문서_v1.20.md#FD-20.1/FD-20.2

FD-20.2(보고서 조회, PDF/CSV 다운로드)는 별도 서버 엔드포인트가 없다 —
원문이 "FD-20.1 응답을 렌더링하는 화면 계층" 몫이라고 명시한다(서버
API는 FD-20.1 하나만 필요).

PLT-19(task-1016): 이 라우터는 원래부터 raw HTTPException을 던지지
않는다(ReportService 예외를 그대로 propagate) — 다른 두 리프
(executions.py/portfolio.py)와 함께 tests/integration/api/
test_no_raw_http_exception.py의 MIGRATED_ROUTERS에 등재만 한다.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.reports_deps import get_report_service
from src.services.auth_service import User
from src.services.report_service import ReportService, ReportSummary

router = APIRouter()


@router.get("")
async def generate_report(
    period_start: date,
    period_end: date,
    execution_id: int | None = None,
    user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportSummary:
    return await service.generate_report(
        user.user_id, period_start, period_end, execution_id=execution_id
    )
