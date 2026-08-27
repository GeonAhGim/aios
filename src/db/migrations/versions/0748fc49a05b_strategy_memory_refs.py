"""strategy_memory_refs

Revision ID: 0748fc49a05b
Revises: 3f5a45c01ee9
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (strategy_memory_refs, 4.6-A Memory-Strategy 출처 연결)
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0748fc49a05b"
down_revision: str | Sequence[str] | None = "3f5a45c01ee9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_memory_refs (
            strategy_id     VARCHAR(100) NOT NULL,
            strategy_version VARCHAR(20) NOT NULL,
            memory_id       UUID NOT NULL REFERENCES memory_entries(memory_id),
            FOREIGN KEY (strategy_id, strategy_version) REFERENCES strategies(strategy_id, version),
            PRIMARY KEY (strategy_id, strategy_version, memory_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE strategy_memory_refs")
