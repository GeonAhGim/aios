"""em3_orders_committed_child_qty

Revision ID: c7f1e3a9d024
Revises: 9d02c197ea36
Create Date: 2026-09-09 12:30:00.000000

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-3 (task-2121
decision). 2026-09-06 audit: no new `parent_orders`/`child_orders` tables --
`orders.parent_order_id`/`algo_run_id` (073beca589d5) stay canonical, this
migration only adds the one aggregate column EM-2's domain rules
(`assert_slice_within_parent_qty`, EM-A1) need but had nowhere to persist:
the running sum of quantity already committed to a parent's children.
`filled_quantity`/`status` are not duplicated here -- a parent's own
`filled_quantity`/`status` columns already exist (210cc26533c7) and
`application/aggregate_parent.py` writes the EM-2 rollup into those same
columns through the existing `transition()` path (no schema change needed
for that half).

`orders` is not WORM (it has its own status-transition guard trigger,
073beca589d5's `oms_enforce_order_transition` -- BEFORE UPDATE, not
append-only) -- `worm_sql()` does not apply here, unlike `order_events`/
`fills` which that same migration correctly does wrap with it. This ALTER
does not touch that trigger; it fires on every `orders` UPDATE regardless
of which columns changed (unconditional `version := OLD.version + 1`), so
a `committed_child_qty`-only UPDATE still gets the optimistic-lock bump for
free without needing an `order_events` row (only a `status` change needs
one, per I6).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c7f1e3a9d024"
down_revision: str | None = "9d02c197ea36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders ADD COLUMN committed_child_qty NUMERIC(30,10) NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_committed_child_qty_bounds "
        "CHECK (committed_child_qty >= 0 AND committed_child_qty <= quantity)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_committed_child_qty_bounds")
    op.execute("ALTER TABLE orders DROP COLUMN committed_child_qty")
