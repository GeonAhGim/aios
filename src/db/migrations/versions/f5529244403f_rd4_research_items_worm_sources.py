"""RD-4 — research_items(WORM) + research_sources + research_item_instruments.

Revision ID: f5529244403f
Revises: c7f1e3a9d024
Create Date: 2026-09-09 00:00:00.000000

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2 module
table (RD-4 row), §4 RD-A2, §9 RD-4.

Three tables. `research_sources` has no `tenant_id` — it is a shared
catalog of external providers (opendart/ecos/fred/...), matching RD-2's
`SourceMeta` contract (`contracts/v1.py`), which itself carries no
`tenant_id` field. `research_items` and `research_item_instruments` are
tenant-scoped: every tenant that ingests the same public document gets
its own row, so `tenant_id UUID NOT NULL REFERENCES tenant(id)` (never
`users(user_id)` — task-1814/FA-0a's recurring incident) is on both, and
cross-tenant lookups are enforced by the adapter's `WHERE tenant_id = $1`
(PLT-30 leaves `research_items` out of its 8-table RLS set, so this
follows the same "policy is code, not RLS" path §10 risk1 already uses
for the legacy `orders`/`positions`/`strategy_executions` tables).

`research_items.hash` is RD-2's content-integrity hash (§1 "출처 추적"),
not an idempotency key — the actual idempotency key
(`(tenant_id, source_id, external_id)` UNIQUE) uses a new `external_id`
column (the source's own document/receipt id, e.g. a DART receipt
number), because RD-2's `ResearchItem` contract does not carry one and
this leaf's file scope excludes `contracts/v1.py`. A correction arrives
under a *different* `external_id` (a new filing/article) and links back
via `revision_of` — so appending a correction never collides with the
idempotency constraint, and the original row is never touched (RD-A2).

WORM reuses L0-3 [[src/core/db/append_only.py]] `worm_sql()` verbatim on
`research_items` only (`research_sources`/`research_item_instruments`
stay mutable — source metadata is updated in place, and entity-mapping
rows may be added by RD-5 batch re-linking). Same two-layer defense as
`b8d5f2a1c3e4`/`c6a3d8f14b92`: REVOKE (defends `PUBLIC`/non-owner roles)
plus a `BEFORE UPDATE OR DELETE` trigger (defends the table owner too,
since Postgres REVOKE never restricts the owner). `research_items` is
new in this leaf, so `4a1d0c0de001`'s `ensure_roles_sql` (which only
granted `aios_app` DML on tables that existed at that migration's run
time) never covered it — this leaf GRANTs `aios_app` explicitly first,
then layers WORM on top, exactly like `b8d5f2a1c3e4`.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "f5529244403f"
down_revision: str | Sequence[str] | None = "c7f1e3a9d024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"

_ITEM_KINDS = ("filing", "news", "macro", "alt")
_REDISTRIBUTION_SCOPES = ("store_full", "store_excerpt", "link_only")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE research_sources (
            source_id             VARCHAR(64) PRIMARY KEY,
            publisher              VARCHAR(200) NOT NULL,
            redistribution_scope   VARCHAR(20) NOT NULL
                CHECK (redistribution_scope IN ({_sql_list(_REDISTRIBUTION_SCOPES)})),
            license_ref             TEXT NOT NULL,
            rate_limit               INT NOT NULL,
            coverage                  TEXT NOT NULL,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON research_sources TO {_APP_ROLE}")

    op.execute(
        f"""
        CREATE TABLE research_items (
            item_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenant(id),
            source_id     VARCHAR(64) NOT NULL REFERENCES research_sources(source_id),
            external_id   VARCHAR(200) NOT NULL,
            kind          VARCHAR(10) NOT NULL
                CHECK (kind IN ({_sql_list(_ITEM_KINDS)})),
            published_at  TIMESTAMPTZ NOT NULL,
            known_at      TIMESTAMPTZ NOT NULL,
            instruments   TEXT[] NOT NULL DEFAULT '{{}}',
            title         TEXT NOT NULL,
            body_ref      TEXT,
            url           TEXT NOT NULL,
            language      VARCHAR(10) NOT NULL,
            hash          CHAR(64) NOT NULL,
            revision_of   UUID REFERENCES research_items(item_id),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, source_id, external_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_research_items_tenant_known "
        "ON research_items (tenant_id, known_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_research_items_revision_of ON research_items (revision_of)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON research_items TO {_APP_ROLE}")
    for statement in worm_sql("research_items"):
        op.execute(statement)

    op.execute(
        """
        CREATE TABLE research_item_instruments (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES tenant(id),
            item_id        UUID NOT NULL REFERENCES research_items(item_id),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (item_id, instrument_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_research_item_instruments_instrument "
        "ON research_item_instruments (instrument_id)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON research_item_instruments TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE research_item_instruments")
    for statement in worm_drop_sql("research_items"):
        op.execute(statement)
    op.execute("DROP TABLE research_items")
    op.execute("DROP TABLE research_sources")
