"""R-46 -- risk_signal: intraday monitor evidence + dedupe.

Revision ID: d6f7b4c3e5a6
Revises: b76f4590b1b8
Create Date: 2026-09-08 08:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-46 (task-2136), §2 table
row 105/109, §6 row 453 (dedupe rule), §9 line 559.

`alembic heads` was confirmed single (`b76f4590b1b8`, EM-6
route_decisions, task-2120) at the start of this leaf's work -- used
as-is for `down_revision` (task decision, serialized behind
task-2130/task-2120 per §C).

`tenant_id` FKs `tenant(id)`, not `users(user_id)` -- FA-0a /
ADR-2026-09-06-G already established `tenant(id)` as the correct target
for brand-new tables (same judgment `ddbd3a33bf30` made for
`pos_borrow_position`); this table starts there directly instead of
carrying the legacy `users` FK forward and needing a later fa0a-style
fix migration.

`data_distrust_state` (also named at spec line 155, same source-table
block) is NOT created here -- it already exists as of `9744695fa220`
(task-103, R-48). This migration creates only `risk_signal` (task
decision).

`dedupe_key` is a plain `VARCHAR(200) UNIQUE` column, not an expression
index -- the value is fully computed by the caller before the INSERT
(`adapters/postgres_signal_repository.dedupe_key_for`:
`f"{type}:{scope_ref}:{floor(as_of,5min)}"`, §6 row 453), so a column
UNIQUE constraint is sufficient for `ON CONFLICT (dedupe_key) DO
NOTHING RETURNING id` (RSK-008).

`safety_control_id` REFERENCES `safety_control(id)` (spec's DDL sketch
at line 155 does not spell out REFERENCES for this column, but it is
unambiguously a foreign-key relationship to an existing table, and
every other cross-entity id column in this schema -- e.g.
`risk_limit_breach.decision_id`, `orders.risk_decision_id` -- is FK'd).
It stays nullable: this leaf's `intraday_monitor.py` inserts the signal
row before the PAUSE control exists (§9 row 109 "signal -> PAUSE", in
that order) and does not loop back to attach the control id afterwards
-- a later leaf can backfill that link if it turns out to be needed.

No RLS policy -- sibling tables in this schema (`risk_limit`,
`risk_rule_bundle`, `safety_control`) all rely on application-level
`WHERE tenant_id = $1` filtering instead of RLS (see
`postgres_limit_repository.list_effective`); `list_open(tenant_id)`
follows the same convention.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "d6f7b4c3e5a6"
down_revision: str | Sequence[str] | None = "b76f4590b1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "risk_signal"
_TYPES = ("DRAWDOWN", "STALE_DATA", "PROVIDER_OUTAGE", "RECON_MISMATCH", "DISTRUST")
_SEVERITIES = ("WARN", "CRITICAL")
_STATES = ("OPEN", "ACKED", "RESOLVED")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenant(id),
            type               VARCHAR(20) NOT NULL
                CHECK (type IN ({_sql_list(_TYPES)})),
            severity           VARCHAR(10) NOT NULL
                CHECK (severity IN ({_sql_list(_SEVERITIES)})),
            dedupe_key         VARCHAR(200) NOT NULL UNIQUE,
            as_of              TIMESTAMPTZ NOT NULL,
            source             TEXT NOT NULL,
            evidence_ref       TEXT,
            state              VARCHAR(10) NOT NULL DEFAULT 'OPEN'
                CHECK (state IN ({_sql_list(_STATES)})),
            safety_control_id UUID REFERENCES safety_control(id),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"CREATE INDEX ix_{_TABLE}_tenant_state ON {_TABLE} (tenant_id, state)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
