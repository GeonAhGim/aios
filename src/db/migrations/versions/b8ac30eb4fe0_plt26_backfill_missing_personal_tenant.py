"""PLT-26/PLT-28 — retroactive backfill: personal tenant for users created
after f4a6b8c0d2e4 (before signup wired the atomic tenant insert, task-2020).

Revision ID: b8ac30eb4fe0
Revises: 47ec4b178f54
Create Date: 2026-09-07 00:38:18.000000

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-26/PLT-28.

`f4a6b8c0d2e4` backfilled a PERSONAL tenant only for users that existed at
that migration's own run time. Every user created afterwards (until this
task wired `AuthService.signup` to insert `users` and `tenant` atomically)
has no matching `tenant` row, so their first foundation write
(consent_record/foundation_audit_event/portfolio_mandate, all FK tenant_id
-> tenant(id)) fails with ForeignKeyViolation. This backfill is a plain
`WHERE NOT EXISTS` gap-fill, idempotent by construction — running it twice
inserts zero rows the second time, and round-tripping upgrade/downgrade/
upgrade leaves the same set of tenant rows both times.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8ac30eb4fe0"
down_revision: str | Sequence[str] | None = "47ec4b178f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_SQL = """
    INSERT INTO tenant (id, kind)
    SELECT u.user_id, 'PERSONAL'
    FROM users u
    WHERE NOT EXISTS (SELECT 1 FROM tenant t WHERE t.id = u.user_id)
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    # Deliberate no-op. A personal tenant backfilled by this migration is
    # structurally identical to one `AuthService.signup` creates going
    # forward (task-2020): both are PERSONAL, both have no
    # `tenant_membership` row (personal tenants resolve without one,
    # task-1090). There is no marker distinguishing "backfilled by this
    # revision" from "created by signup after this revision ran", so a
    # data-deleting downgrade could not target only its own rows without
    # also deleting real users' personal tenants and breaking their
    # already-written consent_record/foundation_audit_event/
    # portfolio_mandate FKs. Leaving the backfilled rows in place keeps
    # upgrade -> downgrade -> upgrade idempotent and safe.
    pass
