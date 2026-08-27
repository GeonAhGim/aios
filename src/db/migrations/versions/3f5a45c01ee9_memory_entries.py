"""memory_entries

Revision ID: 3f5a45c01ee9
Revises: f6335fdcbe80
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Memory Entries, 4.6-A)

작업트리 순서 비고: 문서 번호는 3.9이지만 3.5(strategy_memory_refs)가 이
테이블을 FK로 참조하므로 실제 실행 순서상 먼저 적용한다(10번 문서 3.5 각주
"3.4, 3.9 이후 — memory_entries 먼저 필요"에 따름).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f5a45c01ee9"
down_revision: str | Sequence[str] | None = "f6335fdcbe80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_entries (
            memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            memory_type     VARCHAR(20) NOT NULL,
            content         JSONB NOT NULL,
            source_agent    VARCHAR(100) NOT NULL,
            source_task_id  UUID REFERENCES tasks(task_id),
            confidence      REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
            status          VARCHAR(20) NOT NULL DEFAULT 'UNVERIFIED',
            verified_by     VARCHAR(100),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            verified_at     TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_memory_status ON memory_entries(status)")
    op.execute("CREATE INDEX idx_memory_type ON memory_entries(memory_type)")


def downgrade() -> None:
    op.execute("DROP TABLE memory_entries")
