"""plt35_break_glass_grant -- M7, PLT-35 (task-2682).

Revision ID: b4bb1b750621
Revises: f93d241b4ab6
Create Date: 2026-09-16 02:00:00.000000

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.5 M7 DDL,
§4 I12 ("break-glass grant는 요청자≠승인자, ≤60분, 1회 소비").

The two CHECK constraints are the DB-side half of I12 (the code half is
`src/core/security/break_glass.py`'s conditional UPDATE on `used_at`): a
self-approval or a >60min grant cannot land in the table even if the
application-layer guard in `break_glass.py` is bypassed or has a bug --
this is deliberately the same "DB is the second line of defense" pattern
as `tenant_and_membership`'s partial UNIQUE for the last-owner invariant.

Sole migration of this cycle (task-2682 decision: single head confirmed
before start, `f93d241b4ab6` was the only head).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4bb1b750621"
down_revision: str | None = "f93d241b4ab6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE break_glass_grant (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            requester_id UUID NOT NULL REFERENCES users(user_id),
            approver_id  UUID REFERENCES users(user_id),
            scope        VARCHAR(24) NOT NULL
                         CHECK (scope IN
                             ('kill_switch_override','tenant_read','credential_revoke')),
            reason       TEXT NOT NULL,
            state        VARCHAR(10) NOT NULL DEFAULT 'REQUESTED'
                         CHECK (state IN ('REQUESTED','APPROVED','USED','EXPIRED')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL,
            used_at      TIMESTAMPTZ,
            CHECK (expires_at <= created_at + interval '60 minutes'),
            CHECK (approver_id IS NULL OR approver_id <> requester_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_break_glass_grant_requester ON break_glass_grant(requester_id)")


def downgrade() -> None:
    op.execute("DROP TABLE break_glass_grant")
