"""strategy_executions + orders/positions execution_id

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (Strategy Executions, FD-16). 작업트리 3.14 —
16.1 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_executions (
            id                  BIGSERIAL PRIMARY KEY,
            strategy_id         VARCHAR(100) NOT NULL,
            strategy_version    VARCHAR(20) NOT NULL,
            user_id             UUID NOT NULL REFERENCES users(user_id),
            exchange            VARCHAR(30) NOT NULL,
            mode                VARCHAR(10) NOT NULL CHECK (mode IN ('PAPER','LIVE')),
            allocated_capital   NUMERIC(20,2) NOT NULL,
            currency            VARCHAR(10) NOT NULL DEFAULT 'KRW',
            status              VARCHAR(20) NOT NULL DEFAULT 'PENDING_APPROVAL'
                CHECK (status IN ('PENDING_APPROVAL','RUNNING','PAUSED','RETIRED')),
            paused_by           VARCHAR(20)
                CHECK (paused_by IN ('USER','SAFETY_LAYER')),
            retire_liquidation  VARCHAR(20)
                CHECK (retire_liquidation IN ('IMMEDIATE_MARKET','KEEP_POSITIONS')),
            converted_from_execution_id BIGINT REFERENCES strategy_executions(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at          TIMESTAMPTZ,
            retired_at          TIMESTAMPTZ,
            FOREIGN KEY (strategy_id, strategy_version) REFERENCES strategies(strategy_id, version)
        )
        """
    )
    op.execute("CREATE INDEX idx_strategy_executions_user ON strategy_executions(user_id)")
    op.execute("CREATE INDEX idx_strategy_executions_status ON strategy_executions(status)")
    op.execute(
        "ALTER TABLE orders ADD COLUMN execution_id BIGINT REFERENCES strategy_executions(id)"
    )
    op.execute(
        "ALTER TABLE positions ADD COLUMN execution_id BIGINT REFERENCES strategy_executions(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE positions DROP COLUMN execution_id")
    op.execute("ALTER TABLE orders DROP COLUMN execution_id")
    op.execute("DROP TABLE strategy_executions")
