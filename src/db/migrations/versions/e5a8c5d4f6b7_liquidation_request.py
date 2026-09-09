"""R-51 -- liquidation_request/slice: watchdog LIQUIDATE persistence.

Revision ID: e5a8c5d4f6b7
Revises: d6f7b4c3e5a6
Create Date: 2026-09-09 00:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-51 (task-2357), §2 table row
144 (`watchdog_process.py`), §5 table row 156 (this DDL), §4
`liquidation_request` state table rows 426-432, §5 concurrency table row
454 ("liquidation_request/slice: every state transition is a
conditional_update; workers use SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1").

`alembic heads` was confirmed single (`d6f7b4c3e5a6`, R-46 risk_signal,
task-2136) at the start of this leaf's work -- used as-is for
`down_revision` (task decision, §C migration-leaf serialization: sibling
migration leaf task-1942 was pushed behind this task's `depends_on` for the
same reason -- only one migration leaf open at a time).

Seeds one system-actor `users` row (`WATCHDOG_SYSTEM_ACTOR_ID`).
`watchdog_process.py::_apply_decision` calls `KillSwitchService.activate()`
for both HALT and LIQUIDATE (R-51 DoD (f) -- control creation is delegated
to R-40's one authorized `INSERT INTO safety_control` call site, not
reimplemented here -- see `test_insert_into_safety_control_has_exactly_one_
call_site` in `tests/integration/risk/test_kill_switch_service.py`, I3).
`SafetyScope.GLOBAL` activation requires `actor_is_admin=True` and
`safety_control.actor_subject_id` is `NOT NULL REFERENCES users(user_id)`
(`c7d4e1a9f052`) -- every existing automated caller of
`activate_safety_control()` (`intraday_monitor.py`, `run_reconciliation.py`)
borrows a real *tenant's own* id, because those controls are always scoped
to that one tenant. The watchdog's decision is system-wide
(`compute_system_equity` sums every tenant's RUNNING executions, per
`watchdog_process.py`'s own module docstring), so there is no single tenant
whose id it would be correct to borrow -- it needs its own identity.
`status='SUSPENDED'` blocks the normal login path (`auth_service.py` only
accepts `status='ACTIVE'`) and `password_hash` is not a syntactically valid
bcrypt hash, so this row can never authenticate even if the status check
were ever bypassed.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "e5a8c5d4f6b7"
down_revision: str | Sequence[str] | None = "d6f7b4c3e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_REQUEST_STATES = ("REQUESTED", "PLANNED", "EXECUTING", "DONE", "PARTIAL", "ABORTED")
_SLICE_STATES = ("PENDING", "SENT", "FILLED", "FAILED", "SKIPPED")

# Must stay identical to watchdog_process.py::WATCHDOG_SYSTEM_ACTOR_ID
# (the same constant-parity-check convention as tests/integration/
# test_db_schema.py and the same rationale as 4a1d0c0de005 -- a migration
# must record the SQL as it ran, so it's pinned as a literal string instead
# of an import).
WATCHDOG_SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000002"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO users (user_id, email, password_hash, status, is_platform_admin)
        VALUES (
            '{WATCHDOG_SYSTEM_ACTOR_ID}', 'watchdog-system@aios.internal',
            '!disabled-no-login!', 'SUSPENDED', TRUE
        )
        ON CONFLICT (user_id) DO NOTHING
        """  # noqa: S608 -- the only values are this file's module constants, no user input
    )

    op.execute(
        f"""
        CREATE TABLE liquidation_request (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            safety_control_id UUID NOT NULL REFERENCES safety_control(id),
            scope              VARCHAR(30) NOT NULL,
            scope_ref          VARCHAR(200) NOT NULL DEFAULT '',
            state              VARCHAR(20) NOT NULL DEFAULT 'REQUESTED'
                CHECK (state IN ({_sql_list(_REQUEST_STATES)})),
            plan               JSONB,
            seed_ref           VARCHAR(64),
            requested_by       VARCHAR(40) NOT NULL,
            requested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at       TIMESTAMPTZ,
            fence_token        BIGINT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_liquidation_request_state ON liquidation_request (state)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON liquidation_request TO {_APP_ROLE}")

    op.execute(
        f"""
        CREATE TABLE liquidation_slice (
            id          BIGSERIAL PRIMARY KEY,
            request_id  UUID NOT NULL REFERENCES liquidation_request(id),
            seq         SMALLINT NOT NULL,
            symbol      VARCHAR(30) NOT NULL,
            quantity    NUMERIC(30,10) NOT NULL,
            not_before  TIMESTAMPTZ,
            order_id    UUID REFERENCES orders(order_id),
            state       VARCHAR(10) NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ({_sql_list(_SLICE_STATES)})),
            UNIQUE (request_id, seq)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_liquidation_slice_request_state ON liquidation_slice (request_id, state)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON liquidation_slice TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS liquidation_slice")
    op.execute("DROP TABLE IF EXISTS liquidation_request")
    # The WATCHDOG_SYSTEM_ACTOR_ID users row is deliberately left in place --
    # by the time a real downgrade runs, other already-committed rows (e.g.
    # safety_control.actor_subject_id) may reference this account, so an
    # unconditional DELETE risks failing the downgrade itself with an FK
    # violation.
