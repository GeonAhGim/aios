"""merge dual heads: plt26 personal tenant backfill + la25 pos_borrow_position

Revision ID: 3819cf8a5373
Revises: b8ac30eb4fe0, ddbd3a33bf30
Create Date: 2026-09-07 13:01:55.569752

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '3819cf8a5373'
down_revision: str | Sequence[str] | None = ('b8ac30eb4fe0', 'ddbd3a33bf30')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
