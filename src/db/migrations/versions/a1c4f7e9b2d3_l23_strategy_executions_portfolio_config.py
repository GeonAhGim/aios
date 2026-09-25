"""L23 -- strategy_executions.portfolio_config (migration M2)

Revision ID: a1c4f7e9b2d3
Revises: 9e2b9f93ce15
Create Date: 2026-09-22 00:00:00.000000

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L23.

Expand-only (review-migration checklist #5): both new columns are
nullable/defaulted so an in-flight deploy where old code still writes
`strategy_executions` rows without them keeps working. `portfolio_config`
stores `PortfolioConfig.model_dump(mode="json")` (L17); `NULL` means "no
config set yet", distinct from an empty JSON object. `portfolio_config_version`
backs the same conditional-UPDATE pattern as `strategy_execution_state.
state_version` (M1, I11) -- `portfolio_state.py`, this leaf's other half,
keys its optimistic-lock check off it.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4f7e9b2d3"
down_revision: str | None = "9e2b9f93ce15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions ADD COLUMN portfolio_config JSONB"
    )
    op.execute(
        "ALTER TABLE strategy_executions "
        "ADD COLUMN portfolio_config_version INT NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_executions DROP COLUMN portfolio_config_version")
    op.execute("ALTER TABLE strategy_executions DROP COLUMN portfolio_config")
