"""em3_child_qty_committed_backfill — data-only compensation for task-8661's
pre-fix `committed_child_qty` version gap (task-8890, esc-ci-replay_verify.json).

Revision ID: d4e8f1a29c37
Revises: 6e2b5965124e
Create Date: 2026-09-29 16:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-15
(replay_verify byte-identical guarantee).

Root cause (already fixed in code by task-8661/c660b4f6, this leaf is the
data-side cleanup only): before that fix,
`order_repository.set_committed_child_qty()` wrote a plain `UPDATE orders SET
committed_child_qty = ...` with no companion `order_events` row.
`073beca589d5`'s `oms_enforce_order_transition_trg` bumps `orders.version`
unconditionally on every `UPDATE` (I5) but only demands a companion event
(I6) when `status` actually changes -- a `committed_child_qty`-only update
never changes `status`, so I6 never fired and the version bump went
unaccounted. `src/core/eventstore/projections/orders.py`'s `project()` folds
`orders.version` purely from the `order_events` timeline (`+1` per event,
`+2` for `FILL` to also account for `fills_repository`'s own silent bump) --
any order that went through the pre-fix `set_committed_child_qty` path has
`orders.version` permanently ahead of what its event timeline folds to, so
`scripts/replay_verify.py` reports a byte-level mismatch for it forever,
even though task-8661 already closed the code path that caused it (`git
merge-base --is-ancestor c660b4f6 HEAD` is `yes` on this branch).

Backfill, not a threshold/tolerance change (DECISION_GUIDELINES B-2, PM
decision on task-8890): this INSERT-only migration appends the exact
self-loop `CHILD_QTY_COMMITTED` `order_events` row task-8661's fix would have
written at the time, once per unaccounted version increment, for every order
that actually took the buggy path -- `orders.committed_child_qty > 0` is the
precondition that method is the only writer of, so it scopes the backfill to
orders that could plausibly have hit the bug, not to every order with *some*
legacy version drift (other legacy write paths --
`src/services/order_service/repository.py`,
`src/services/order_service/fenced_submit.py`,
`src/services/safety/open_order_sweeper.py` -- are pre-cutover code this
migration does not touch or claim to fix). Orders already fixed forward by
task-8661 (no gap left) or never touched by `set_committed_child_qty`
(`committed_child_qty = 0`, the column default) get zero rows inserted --
idempotent by construction, safe to run against an empty dev DB (task-8890
note: local worker DB has 0 orders, so this is a structural no-op there).

`payload_hash` on the backfilled rows cannot reproduce the original runtime
call's exact `_child_qty_committed_payload_hash(order_id, expected_version,
committed_child_qty)` input (the intermediate `expected_version`/
`committed_child_qty` values for each individual historical bump are not
recoverable from a `version`-only column) -- this is fine because
`scripts/replay_verify.py`'s digest (`src/core/eventstore/replay.py
digest_state`) is computed only over `{status, version, filled_quantity,
average_fill_price, fee_total, fee_currency}` (`_ORDER_FIELDS`), never over
`payload_hash`; the backfilled hash only needs to satisfy the `CHAR(64) NOT
NULL` column, not match any specific runtime value.

Conditionally irreversible (`downgrade()` raises only when it would need to
undo something): `order_events` is WORM (`073beca589d5`'s
`worm_sql("order_events")` -- `BEFORE UPDATE OR DELETE ... RAISE EXCEPTION`,
fires unconditionally, even for the table owner, per that migration's own
docstring). Deleting the rows this migration appends on downgrade would
require temporarily dropping that trigger, which is the exact anti-pattern
`docs design` review guidance (and the FA-4/FA-0d precedent,
`963d5f3cfb1b`/`cdb114b6903f`, which instead leaves `pos_journal` permanently
on the old key format) already rules out for WORM tables. An audit trail that
can be revoked on `downgrade` is not actually an audit trail -- the correct
WORM-consistent behavior is to refuse the fake rollback outright, the same
tradeoff FA-0d already accepted for `pos_journal`.

`downgrade()` first checks whether `upgrade()` actually inserted any
`reason_code = EM3_CHILD_SLICE_COMMIT_BACKFILL_TASK8890` rows. If none exist
(e.g. an empty dev/test DB with no `committed_child_qty > 0` order -- the
structural no-op case this file's docstring already calls out above), there
is nothing to protect and downgrade is a true no-op. Only when compensating
rows are actually present does it raise -- deep-downgrade migration round-trip
tests (`tests/integration/foundation/entities/test_migration_fa3_deepen.py`)
start from an empty DB and must be able to pass through this revision on the
way to an older target; a migration that unconditionally refuses to downgrade
even when it changed nothing would make every such round trip permanently
red the moment this revision became head (task-9287).
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "d4e8f1a29c37"
down_revision: str | Sequence[str] | None = "6e2b5965124e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASON_CODE = "EM3_CHILD_SLICE_COMMIT_BACKFILL_TASK8890"

# `:reason_code` is a bound parameter (never interpolated) -- everything
# else is a fixed constant (table/column names).
_BACKFILL_SQL = r"""
WITH gaps AS (
    SELECT
        o.order_id,
        o.status,
        o.updated_at,
        o.version - COALESCE(fold.replayed_version, 0) AS gap
    FROM orders o
    LEFT JOIN LATERAL (
        SELECT SUM(CASE WHEN oe.event = 'FILL' THEN 2 ELSE 1 END) AS replayed_version
        FROM order_events oe
        WHERE oe.order_id = o.order_id
    ) AS fold ON true
    WHERE o.committed_child_qty > 0
)
INSERT INTO order_events (
    order_id, from_status, to_status, event, reason_code, actor_subject_id,
    trace_id, occurred_at, payload_hash
)
SELECT
    g.order_id, g.status, g.status, 'CHILD_QTY_COMMITTED',
    :reason_code, 'system', gen_random_uuid(), g.updated_at,
    encode(
        sha256(convert_to(g.order_id::text || '\:task8890-backfill\:' || s.n::text, 'UTF8')),
        'hex'
    )
FROM gaps g
CROSS JOIN LATERAL generate_series(1, g.gap) AS s(n)
WHERE g.gap > 0
"""


class Em3ChildQtyBackfillIrreversibleError(RuntimeError):
    """`order_events` is WORM (I7) -- the self-loop `CHILD_QTY_COMMITTED` rows
    this migration appends cannot be un-appended without bypassing that
    guard. See module docstring."""


def upgrade() -> None:
    op.get_bind().execute(text(_BACKFILL_SQL), {"reason_code": _REASON_CODE})


def downgrade() -> None:
    bind = op.get_bind()
    inserted = bind.execute(
        text("SELECT count(*) FROM order_events WHERE reason_code = :reason_code"),
        {"reason_code": _REASON_CODE},
    ).scalar_one()
    if not inserted:
        # Structural no-op: upgrade() found no `committed_child_qty > 0` order
        # to compensate (empty dev/test DB, per the module docstring), so
        # there is nothing WORM to protect -- downgrade has nothing to undo.
        return
    raise Em3ChildQtyBackfillIrreversibleError(
        "d4e8f1a29c37: order_events is WORM (I7) -- the compensating "
        "CHILD_QTY_COMMITTED self-loop rows this revision inserted cannot be "
        "reverted in downgrade -- temporarily dropping the append-only "
        "trigger to allow it is the anti-pattern review-migration checklist "
        "item 8 forbids. Same tradeoff FA-0d (cdb114b6903f) already accepted "
        "leaving pos_journal permanently on the old key format."
    )
