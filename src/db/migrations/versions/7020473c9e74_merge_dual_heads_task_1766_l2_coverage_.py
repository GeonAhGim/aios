"""merge dual heads: task-1766 l2 coverage venue widen + task-1768 calendar source widen

Revision ID: 7020473c9e74
Revises: 4b19195124bb, 6325757fd371
Create Date: 2026-09-07 13:54:27.490294

The two leaves are heads that branched independently off the same parent
(3819cf8a5373) — task-1766 (coverage_spans venue/timeframe CHECK
expansion) and task-1768 (md_venue_calendar_day.source column-width
expansion) touch different tables, so there's no schema conflict. This
is a pure merge stub (empty upgrade/downgrade) that folds the DAG back
into a single head — the same pattern as 3819cf8a5373.
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
