"""tasks

Revision ID: a7c02fa80d22
Revises:
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Tasks, 4.3 AIOSTask)

편차: DDL 원문은 user_id UUID REFERENCES users(user_id)이지만, users 테이블은
작업트리 11.1(섹션 11, 계정)에서 생성된다 — 이 마이그레이션은 "기반"
단계(1~8)에 속해 users보다 먼저 실행되므로, FK 없이 컬럼만 두고 11.1에서
ALTER TABLE로 제약을 추가한다(capability_token_id를 3.3에서 다루는 것과
동일한 순환참조 회피 기법).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c02fa80d22"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tasks (
            task_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            parent_task_id  UUID REFERENCES tasks(task_id),
            user_id         UUID,  -- FK: 11.1에서 ALTER TABLE로 추가
            objective       TEXT NOT NULL,
            assigned_agent  VARCHAR(100) NOT NULL,
            required_permission_level SMALLINT NOT NULL
                CHECK (required_permission_level BETWEEN 0 AND 6),
            status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            input_payload   JSONB NOT NULL DEFAULT '{}',
            output_result   JSONB,
            retry_count     INT NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ,

            -- 16.2 Capability Token 연동 — FK: 3.3(capability_tokens)에서 ALTER TABLE로 추가
            capability_token_id UUID
        )
        """
    )
    op.execute("CREATE INDEX idx_tasks_status ON tasks(status)")
    op.execute("CREATE INDEX idx_tasks_assigned_agent ON tasks(assigned_agent)")


def downgrade() -> None:
    op.execute("DROP TABLE tasks")
