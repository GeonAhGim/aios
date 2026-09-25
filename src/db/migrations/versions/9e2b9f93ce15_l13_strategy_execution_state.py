"""L13 -- strategy_execution_state table (migration M1)

Revision ID: 9e2b9f93ce15
Revises: 627bd92ec750
Create Date: 2026-09-22 00:00:00.000000

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §3.7 M1, §9 L13.

New table only -- nothing existing is touched, so no expand/contract split
is needed. `execution_id` is the PK (one persisted `StrategyStateMemory`
row per `strategy_executions` row, L08); FK `ON DELETE CASCADE` so retiring
an execution's row doesn't strand orphaned state. `state_version` backs
I11's conditional-UPDATE contract (`strategy_state_store.py`, this leaf's
other half) -- readers/writers key their optimistic-lock check off it.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e2b9f93ce15"
down_revision: str | None = "627bd92ec750"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_execution_state (
            execution_id   BIGINT PRIMARY KEY
                REFERENCES strategy_executions(id) ON DELETE CASCADE,
            state          JSONB NOT NULL,
            state_version  INT NOT NULL DEFAULT 0,
            schema_version VARCHAR(10) NOT NULL DEFAULT 'ssm-v1',
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE strategy_execution_state")
