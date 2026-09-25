"""L32 -- market_bar_snapshot: input bar snapshot storage (§3.7 M4).

Revision ID: 6e2b5965124e
Revises: a1c4f7e9b2d3

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.4
(`adapters/postgres_snapshot_repository.py` row), §3.7 M4
(`snapshot_hash VARCHAR(64) PK, symbol, exchange, timeframe, from_time,
to_time, bar_count INT, source VARCHAR(50), as_of TIMESTAMPTZ, bars JSONB
NOT NULL, created_at. REVOKE UPDATE, DELETE`), §9 L32 ("저장->로드->해시
동일").

`snapshot_hash` is `backtest.domain.snapshot.compute_bar_snapshot_hash`'s
output (bar sequence + source + as_of, sha256 hex) -- reused verbatim as
the primary key, so `INSERT ... ON CONFLICT (snapshot_hash) DO NOTHING`
(§5 "market_bar_snapshot INSERT" 해시 dedup 행) makes re-saving the same
input snapshot a no-op rather than a duplicate row or a conflict error, and
two concurrent first-saves of the same snapshot both succeed silently.

WORM via the shared L0-5 generator (`worm_sql`) -- same two-layer defense
(REVOKE UPDATE/DELETE from PUBLIC plus a `BEFORE UPDATE OR DELETE` trigger
that fires even for the table owner) as `c3f8a1d29b6e_ai10_experiments_worm.py`
and `f5529244403f_rd4_research_items_worm_sources.py`. `aios_app` is granted
SELECT/INSERT only -- UPDATE/DELETE are never granted, so the REVOKE only
defends non-owner/PUBLIC roles and the trigger is the actual enforcement
layer for the table owner.

This leaf reissues `task-3357` (ND-17 재발행, task-6452): the original task
was marked done but its commit is not an ancestor of `origin/main`
(`done_commit_unreachable`), so neither this migration nor the adapter it
backs ever actually landed.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "6e2b5965124e"
down_revision: str | Sequence[str] | None = "a1c4f7e9b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "market_bar_snapshot"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            snapshot_hash  VARCHAR(64) PRIMARY KEY,
            symbol         VARCHAR(50) NOT NULL,
            exchange       VARCHAR(50) NOT NULL,
            timeframe      VARCHAR(10) NOT NULL,
            from_time      TIMESTAMPTZ NOT NULL,
            to_time        TIMESTAMPTZ NOT NULL,
            bar_count      INT NOT NULL CHECK (bar_count > 0),
            source         VARCHAR(50) NOT NULL,
            as_of          TIMESTAMPTZ NOT NULL,
            bars           JSONB NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{_TABLE}_symbol_tf ON {_TABLE} (symbol, exchange, timeframe)"
    )
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP TABLE {_TABLE}")
