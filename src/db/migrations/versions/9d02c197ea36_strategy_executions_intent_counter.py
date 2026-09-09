"""strategy_executions_intent_counter

Revision ID: 9d02c197ea36
Revises: cdb114b6903f
Create Date: 2026-09-09 00:00:00.000000

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-10 [FROZEN_PAPER_ONLY]

task-2351 -- backs `OrderIdempotencyScope.intent_seq` (services/oms/domain/
idempotency.py, L4-03) so `Executor.execute()` can derive a deterministic
`client_order_id` per intent instead of self-generating one from a
timestamp. Defaults to 0 for existing rows; incrementing it when a new
intent begins (FSM transition into a PENDING state) is a follow-up leaf --
this migration only adds the column, `Executor` only reads it.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d02c197ea36"
down_revision: str | Sequence[str] | None = "cdb114b6903f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions ADD COLUMN intent_counter INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_executions DROP COLUMN intent_counter")
