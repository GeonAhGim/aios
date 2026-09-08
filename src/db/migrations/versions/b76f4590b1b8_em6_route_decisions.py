"""EM-6 -- route_decisions: immutable venue-routing evidence table.

Revision ID: b76f4590b1b8
Revises: b2927f5a25c2
Create Date: 2026-09-08 07:00:00.000000

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md#EM-6 (task-2120
decision, PM 2026-09-08).

Re-parented onto `b2927f5a25c2` (DC-21 `instrument_attributes`, task-2130)
just before push -- that migration landed on `origin/main` concurrently
while this leaf's original parent (`8425d20c192e`) was still the head, so
`down_revision` is updated to the actual single head at push time rather
than forking two heads (decision item 1).

New table, not a reuse of `risk_decision`/`policy_decision` (decision item
2) -- those are risk/compliance gate verdicts, this is routing evidence
(which venue an order was sent to, and why). WORM via the shared L0-3
generator (`worm_sql`) -- no bespoke trigger/REVOKE, same pattern as
`8425d20c192e_fa12_ledger_abor_snapshot.py`.

`order_id` is a bare `UUID` with no FK -- routing decisions are made before
an order necessarily exists as an `orders` row (this leaf does not submit
orders, decision item 3; wiring an order to its routing decision is EM-15's
job). `UNIQUE (order_id)` is the DoD(3) idempotency guard: a second
`route_order` call for the same `order_id` hits this constraint via
`ON CONFLICT (order_id) DO NOTHING`, and
`adapters/postgres_route_decision_repository.py` re-selects the existing
row instead of raising -- unlike FA-12's re-close rejection, a duplicate
routing attempt is not an error, it is the same decision being asked for
again.

`candidates_snapshot`/`score_snapshot`/`weights_snapshot` (JSONB) hold the
raw EM-4 inputs, EM-5's full ranked output, and the weights used --
together sufficient to re-run `domain.route.venue_scoring.rank_venues` and
reproduce `venue`/`reason_codes`/`expected_cost_bps` exactly (DoD 1).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "b76f4590b1b8"
down_revision: str | Sequence[str] | None = "b2927f5a25c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "route_decisions"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            decision_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            order_id            UUID NOT NULL,
            venue               TEXT NOT NULL,
            reason_codes        TEXT[] NOT NULL,
            expected_cost_bps   NUMERIC NOT NULL,
            candidates_snapshot JSONB NOT NULL,
            score_snapshot      JSONB NOT NULL,
            weights_snapshot    JSONB NOT NULL,
            decided_at          TIMESTAMPTZ NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (order_id)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP TABLE {_TABLE}")
