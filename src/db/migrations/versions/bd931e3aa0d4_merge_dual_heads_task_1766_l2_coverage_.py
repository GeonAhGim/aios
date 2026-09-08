"""merge dual heads: task-1766 l2 coverage + task-1987 fa0a batch b calendar source

Revision ID: bd931e3aa0d4
Revises: 7020473c9e74, 2e35eea547f2
Create Date: 2026-09-07 14:04:18.141515

Two workers independently targeted the same parent (6325757fd371,
task-1768's calendar source width expansion) for a merge and each
created their own merge stub, producing yet another dual head
(7020473c9e74 = the task-1766-side merge, 2e35eea547f2 = the
task-1987-side merge). This is a pure merge stub (empty
upgrade/downgrade) that folds it back into a single head again.
"""
from collections.abc import Sequence

revision: str = "bd931e3aa0d4"
down_revision: str | Sequence[str] | None = ("7020473c9e74", "2e35eea547f2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
