"""Merge heads — FA-0a batch B (f6b25409405e) and calendar source widen (6325757fd371).

Revision ID: 2e35eea547f2
Revises: f6b25409405e, 6325757fd371
Create Date: 2026-09-07 06:00:00.000000

No-op merge: both branches landed independently on top of 3819cf8a5373 and
touch disjoint tables (tenant_id FK correction on ledger/positions/
market-data tables vs. `md_venue_calendar_day.source` column width), so
there is nothing to reconcile.
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2e35eea547f2"
down_revision: str | Sequence[str] | None = ("f6b25409405e", "6325757fd371")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
