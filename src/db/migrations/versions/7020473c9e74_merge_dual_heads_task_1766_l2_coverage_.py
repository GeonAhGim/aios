"""merge dual heads: task-1766 l2 coverage venue widen + task-1768 calendar source widen

Revision ID: 7020473c9e74
Revises: 4b19195124bb, 6325757fd371
Create Date: 2026-09-07 13:54:27.490294

두 리프가 같은 부모(3819cf8a5373)에서 독립적으로 갈라져 나온 head다 —
task-1766(coverage_spans venue/timeframe CHECK 확장)과 task-1768
(md_venue_calendar_day.source 컬럼 폭 확장)은 서로 다른 테이블을 건드려
스키마 충돌이 없다. 순수 병합 스텁(빈 upgrade/downgrade)으로 DAG를
단일 head로 되돌린다 — 3819cf8a5373와 동일 패턴.
"""
from collections.abc import Sequence

revision: str = "7020473c9e74"
down_revision: str | Sequence[str] | None = ("4b19195124bb", "6325757fd371")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
