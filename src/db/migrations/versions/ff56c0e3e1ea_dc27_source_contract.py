"""DC-27 — source_contract (source contract tier / redistribution scope).

Revision ID: ff56c0e3e1ea
Revises: b5bf8da8e058
Create Date: 2026-09-07

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. A new table placed alongside `entitlements` (9049e2b6b0b7:100) —
`entitlements` answers "can this tenant see this venue", while
`source_contract` answers "at what tier and redistribution scope has the
platform contracted for this source" (different axes, so they are not
merged into one table; D1's "no new context" refers to the domain code
layer, not a requirement to merge tables).

`source_id` is the PK — for D1's "tier upgrade is a row update" DoD to
hold (switching a personal contract to a business contract must be a single
UPDATE to this row, not a new row, so adapter code never changes).
`credential_ref` stores only a keyring-handle string — the raw key is not
in this table (D1 "the real key is not here").

`capability` is JSONB (asset class/resolution/whether corporate actions are
covered, D6) — vocabularies differ per source, so there is no fixed
enumeration to enforce via a CHECK constraint.
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceContractTier,
)

# revision identifiers, used by Alembic.
revision: str = "ff56c0e3e1ea"
down_revision: str | Sequence[str] | None = "b5bf8da8e058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE source_contract (
            source_id             VARCHAR(64) PRIMARY KEY,
            tier                  VARCHAR(20) NOT NULL
                CHECK (tier IN ({_sql_enum_members(SourceContractTier)})),
            credential_ref        VARCHAR(255) NOT NULL,
            redistribution_scope  VARCHAR(20) NOT NULL DEFAULT 'NONE'
                CHECK (redistribution_scope IN ({_sql_enum_members(RedistributionScope)})),
            rate_limit            INTEGER NOT NULL CHECK (rate_limit >= 0),
            quota                 INTEGER NOT NULL CHECK (quota >= 0),
            valid_from            TIMESTAMPTZ NOT NULL,
            valid_to              TIMESTAMPTZ,
            capability            JSONB NOT NULL,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (valid_to IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON source_contract TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE source_contract")
