"""Merge heads — FA-0a batch A (ccfb229d760d) and dc27_source_contract (ff56c0e3e1ea).

Revision ID: 24af9c37d76f
Revises: ccfb229d760d, ff56c0e3e1ea
Create Date: 2026-09-07 09:02:30.698847

No-op merge: both branches landed independently on top of b5bf8da8e058 and
touch disjoint tables, so there is nothing to reconcile.
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "24af9c37d76f"
down_revision: str | Sequence[str] | None = ("ccfb229d760d", "ff56c0e3e1ea")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
