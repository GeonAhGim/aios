"""AI-10 -- experiments: append-only experiment ledger table.

Revision ID: c3f8a1d29b6e
Revises: 0895391e36f5

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-10
(`Experiment{experiment_id, reproducibility_key, kind, inputs_hash, metrics,
artifacts, parent_id, created_by}`, "adapters/postgres_repository.py +
migration -- append-only (WORM trigger, L0-3 reuse)"), §9 AI-10 DoD
("append-only proof").

`tenant_id` has no FK, matching this same spec's own `0895391e36f5`
(`agent_token.tenant_id`) rather than the `research_data`/`ems` modules'
`tenant(id)` FK convention -- kept consistent within
`L4_ai_research_strategy_factory_v1.0` (task-2645 decision).

`parent_id` self-references `experiments(experiment_id)` -- the DB enforces
"parent must already exist" as a real FK (a child row can only be inserted
after its parent commits), which is strictly stronger than an application-
level existence check alone; `domain/lineage.py::validate_new_experiment`
still runs first so the caller gets a domain error, not a raw FK violation,
on the common dangling-parent mistake.

WORM via the shared L0-3 generator (`worm_sql`) -- REVOKE UPDATE/DELETE from
PUBLIC plus a `BEFORE UPDATE OR DELETE` trigger that fires even for the
table owner (same two-layer defense as `b76f4590b1b8_em6_route_decisions.py`
and `f5529244403f_rd4_research_items_worm_sources.py`). `aios_app` is
granted SELECT/INSERT only -- UPDATE/DELETE are never granted at all, so the
REVOKE only defends non-owner/PUBLIC roles and the trigger is the actual
enforcement layer for the owner.
"""

from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "c3f8a1d29b6e"
down_revision: str | Sequence[str] | None = "0895391e36f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "experiments"
_KINDS = ("backtest", "sweep", "walk_forward", "paper")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            experiment_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL,
            reproducibility_key  CHAR(64) NOT NULL,
            kind                 VARCHAR(20) NOT NULL
                CHECK (kind IN ({_sql_list(_KINDS)})),
            inputs_hash          CHAR(64) NOT NULL,
            metrics              JSONB NOT NULL,
            artifacts            TEXT[] NOT NULL DEFAULT '{{}}',
            parent_id            UUID REFERENCES {_TABLE}(experiment_id),
            created_by           UUID NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{_TABLE}_tenant_repro_key ON {_TABLE} (tenant_id, reproducibility_key)"
    )
    op.execute(
        f"CREATE INDEX ix_{_TABLE}_parent ON {_TABLE} (parent_id) WHERE parent_id IS NOT NULL"
    )
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP TABLE {_TABLE}")
