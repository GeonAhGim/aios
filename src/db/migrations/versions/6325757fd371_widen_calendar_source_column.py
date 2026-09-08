"""task-1768 — widen `md_venue_calendar_day.source` to a length that can hold a URL.

Revision ID: 6325757fd371
Revises: 3819cf8a5373
Create Date: 2026-09-07 05:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9.2 LA-10, §10 R4.
ADR-2026-09-06-H D3: resolve the exchange calendar's `source: UNVERIFIED`
into a primary source (the exchange's official announcement/data URL).
The existing `VARCHAR(50)` was sized only for a short placeholder
("UNVERIFIED") and isn't enough to hold the actual source URL together
with the collection timestamp (`yaml_calendar_source.load_calendar`
concatenates the two into a single string for this column — a PM
decision ruled that adding a separate column is out of scope for RD-21).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6325757fd371"
down_revision: str | Sequence[str] | None = "3819cf8a5373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE md_venue_calendar_day ALTER COLUMN source TYPE VARCHAR(500)")


def downgrade() -> None:
    op.execute("ALTER TABLE md_venue_calendar_day ALTER COLUMN source TYPE VARCHAR(50)")
