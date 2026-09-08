"""DC-21 -- instrument_attributes (append-only reference-data known_at ledger).

Revision ID: b2927f5a25c2
Revises: 8425d20c192e
Create Date: 2026-09-08 00:00:00.000000

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Section 9.10 DC-21 (depends on DC-4 `instruments`, `dbaf260f2917`).

Corrections to reference-data attributes (e.g. `lot_size`) are new rows
stamped with `known_at`, never UPDATEs -- `domain/point_in_time.py`
(`latest_attributes_as_of`) picks the row with the greatest `known_at <=
as_of` per `attr_key`. Append-only is enforced by the same WORM trigger
generator the ledger tables use (`core/db/append_only.worm_sql`) rather
than a new implementation -- the trigger fires regardless of role,
including the table owner, so REVOKE alone (bypassable by an owner) is not
the real defense.

The primary key is `(instrument_id, attr_key, known_at)` -- it also
forbids two corrections for the same key landing on the exact same
instant, which would otherwise make "the latest row" ambiguous. A
secondary index adds the `known_at DESC` ordering the DC-21 DoD's
point-in-time lookup relies on (`ORDER BY known_at DESC LIMIT 1` per
`attr_key`), since a plain ascending PK index still requires a backward
scan for that access pattern.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "b2927f5a25c2"
down_revision: str | Sequence[str] | None = "8425d20c192e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "instrument_attributes"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            instrument_id VARCHAR(26) NOT NULL REFERENCES instruments(instrument_id),
            attr_key      VARCHAR(100) NOT NULL,
            attr_value    TEXT NOT NULL,
            known_at      TIMESTAMPTZ NOT NULL,
            recorded_by   UUID REFERENCES users(user_id),
            PRIMARY KEY (instrument_id, attr_key, known_at)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{_TABLE}_lookup "
        f"ON {_TABLE} (instrument_id, attr_key, known_at DESC)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP TABLE {_TABLE}")
