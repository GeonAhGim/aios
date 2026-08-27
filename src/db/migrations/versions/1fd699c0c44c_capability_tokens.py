"""capability_tokens

Revision ID: 1fd699c0c44c
Revises: a7c02fa80d22
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Capability Tokens, 16.2)
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1fd699c0c44c"
down_revision: str | Sequence[str] | None = "a7c02fa80d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE capability_tokens (
            token_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_id         UUID NOT NULL REFERENCES tasks(task_id),
            repository      VARCHAR(100) NOT NULL,
            branch          VARCHAR(100) NOT NULL,
            allowed_paths   JSONB NOT NULL,
            operations      JSONB NOT NULL,
            network_access  BOOLEAN NOT NULL DEFAULT FALSE,
            secrets_scope   JSONB NOT NULL DEFAULT '[]',
            issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            ttl_seconds     INT NOT NULL,
            revoked_at      TIMESTAMPTZ,
            revoked_reason  VARCHAR(50)
        )
        """
    )
    op.execute("CREATE INDEX idx_cap_tokens_task ON capability_tokens(task_id)")
    # tasks.capability_token_id의 실제 FK 제약 (순환참조 방지 위해 별도 ALTER)
    op.execute(
        """
        ALTER TABLE tasks ADD CONSTRAINT fk_tasks_capability_token
            FOREIGN KEY (capability_token_id) REFERENCES capability_tokens(token_id)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT fk_tasks_capability_token")
    op.execute("DROP TABLE capability_tokens")
