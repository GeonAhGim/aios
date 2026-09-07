"""FA-0b — retroactive fix: drop the single-portfolio-per-tenant assumption
on portfolio_mandate.

Revision ID: 47ec4b178f54
Revises: 24af9c37d76f
Create Date: 2026-09-07 00:10:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0b
(§9 row 111). PM 2026-09-07 decision (task-1941) — started with `alembic
heads` at 24af9c37d76f (single head after the FA-0a batch A / DC-27 merge)
and used that as down_revision.

`d8e8e4ba2365:45` created `portfolio_mandate` with `UNIQUE (tenant_id)` —
correct only under the old "exactly one portfolio per tenant" assumption.
This migration replaces it with `UNIQUE (tenant_id, portfolio_id)` so a
tenant can hold one mandate per portfolio.

Backfill rule (same pattern as `789c138f13fe` FA-3): reuse FA-1
`domain/defaults.py`'s deterministic `default_portfolio_id(user_id)` — no
new id-generation rule is invented here. `portfolio_mandate.tenant_id`
still FKs `users(user_id)` (this table was out of scope for the FA-0a
batch A tenant-FK fix), so the existing column value itself is the
`user_id` argument `default_portfolio_id` expects. Every pre-existing row
gets a real, reproducible portfolio_id — none are guessed and none are
left NULL, since NULL wouldn't actually be constrained by the new UNIQUE
(Postgres treats each NULL as distinct).
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from src.foundation.entities.domain.defaults import default_portfolio_id

# revision identifiers, used by Alembic.
revision: str = "47ec4b178f54"
down_revision: str | Sequence[str] | None = "24af9c37d76f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE portfolio_mandate ADD COLUMN portfolio_id UUID")
    _backfill_portfolio_id()
    op.execute("ALTER TABLE portfolio_mandate ALTER COLUMN portfolio_id SET NOT NULL")
    op.execute("ALTER TABLE portfolio_mandate DROP CONSTRAINT portfolio_mandate_tenant_id_key")
    op.execute(
        "ALTER TABLE portfolio_mandate ADD CONSTRAINT portfolio_mandate_tenant_id_portfolio_id_key "
        "UNIQUE (tenant_id, portfolio_id)"
    )


def _backfill_portfolio_id() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, tenant_id FROM portfolio_mandate")).fetchall()
    for row_id, tenant_id in rows:
        bind.execute(
            text("UPDATE portfolio_mandate SET portfolio_id = :portfolio_id WHERE id = :id"),
            {"portfolio_id": default_portfolio_id(tenant_id), "id": row_id},
        )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE portfolio_mandate DROP CONSTRAINT portfolio_mandate_tenant_id_portfolio_id_key"
    )
    op.execute(
        "ALTER TABLE portfolio_mandate ADD CONSTRAINT portfolio_mandate_tenant_id_key "
        "UNIQUE (tenant_id)"
    )
    op.execute("ALTER TABLE portfolio_mandate DROP COLUMN portfolio_id")
