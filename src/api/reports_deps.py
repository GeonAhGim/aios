"""20번대 — 보고서 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.services.report_service import ReportService

from .deps import get_pool


def get_report_service(pool: asyncpg.Pool = Depends(get_pool)) -> ReportService:
    return ReportService(pool)
