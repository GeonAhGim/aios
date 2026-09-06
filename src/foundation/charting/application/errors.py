"""Charting application 계층 공용 예외.

새 taxonomy를 만들지 않는다(task-1557 decision) — 둘 다 기존 ErrorCode로
접는다: `ChartLayoutNotFoundError`/`CrossTenantChartLayoutAccessError`는
RESOURCE_NOT_FOUND(404, 타 테넌트도 존재를 흘리지 않도록 동일 코드),
동시성 충돌은 새 클래스 없이 `src.core.db.conditional_write.
ConcurrencyConflictError`(이미 STATE_CONCURRENCY_CONFLICT/409로 전역
등록됨)를 그대로 재사용한다."""
from __future__ import annotations


class ChartLayoutNotFoundError(Exception):
    pass


class CrossTenantChartLayoutAccessError(Exception):
    pass


class ChartIndicatorTemplateNotFoundError(Exception):
    pass


class CrossTenantChartIndicatorTemplateAccessError(Exception):
    pass
