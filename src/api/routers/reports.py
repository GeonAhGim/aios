"""20번 — 기간별 보고서 API 라우터 (FD-20.1).

Spec: 기능설계문서_v1.20.md#FD-20.1/FD-20.2

FD-20.2(보고서 조회, PDF/CSV 다운로드)는 별도 서버 엔드포인트가 없다 —
원문이 "FD-20.1 응답을 렌더링하는 화면 계층" 몫이라고 명시한다(서버
API는 FD-20.1 하나만 필요).
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
