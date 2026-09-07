"""Merge heads — FA-0a batch C (94da854f522f) and FA-10 bitemporal projections (a2c4f9e1b3d5).

Revision ID: 6877947783a6
Revises: 94da854f522f, a2c4f9e1b3d5
Create Date: 2026-09-07 15:28:48.259852

No-op merge: both branches landed independently on top of c6a3d8f14b92 and
touch disjoint tables, so there is nothing to reconcile.
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6877947783a6"
down_revision: str | Sequence[str] | None = ("94da854f522f", "a2c4f9e1b3d5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
