"""RD-20 — md_corporate_action_filing (append-only) + unprocessed-filing queue.

Revision ID: a1f3c9d2e5b7
Revises: bd931e3aa0d4
Create Date: 2026-09-07

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D3 (domestic corporate actions are self-built from the
original electronic-disclosure filings).

`md_corporate_action_filing` is a separate table from
`md_corporate_action` (LA-12, task-451, UNIQUE on
`(instrument_id, action_type, ex_date)` — "the one currently valid
value"). This table accumulates the full filing-receipt history,
including corrections, append-only — the only idempotency key is the
DART filing receipt number (`source_ref`), and
`(instrument_id, action_type, ex_date)` is deliberately left without a
UNIQUE constraint (it's expected for a correction to produce multiple
rows under the same key). `aios_app` is not granted UPDATE/DELETE, so
append-only is enforced by DB privileges themselves — proven by wiring,
not by code review — even against mistakes or bugs in the application
layer.

`research_opendart_unprocessed_filing` is a queue that keeps filings
that failed parsing or were rejected by the source contract instead of
letting them silently disappear. `raw_payload` holds only
`OpenDartFiling`'s structured fields (no raw filing text), so it does
not violate the DC-27 redistribution restriction.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c9d2e5b7"
down_revision: str | Sequence[str] | None = "bd931e3aa0d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE md_corporate_action_filing (
            filing_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id       UUID NOT NULL REFERENCES md_instrument(instrument_id),
            action_type         VARCHAR(20) NOT NULL
                CHECK (action_type IN ('SPLIT','REVERSE_SPLIT','CASH_DIVIDEND','MERGER')),
            ex_date             DATE NOT NULL,
            ratio               NUMERIC(20,10) NOT NULL CHECK (ratio > 0),
            cash_amount         NUMERIC(20,10),
            source_ref          VARCHAR(200) NOT NULL,
            known_at            TIMESTAMPTZ NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_md_corporate_action_filing_instrument "
        "ON md_corporate_action_filing (instrument_id, action_type, ex_date, known_at)"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON md_corporate_action_filing TO {_APP_ROLE}"
    )

    op.execute(
        """
        CREATE TABLE research_opendart_unprocessed_filing (
            id              BIGSERIAL PRIMARY KEY,
            source_id       VARCHAR(64) NOT NULL,
            raw_payload     JSONB NOT NULL,
            reason          TEXT NOT NULL,
            received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved        BOOLEAN NOT NULL DEFAULT FALSE,
            resolved_at     TIMESTAMPTZ
        )
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON research_opendart_unprocessed_filing TO {_APP_ROLE}"
    )
    op.execute(
        f"GRANT USAGE, SELECT ON research_opendart_unprocessed_filing_id_seq TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE research_opendart_unprocessed_filing")
    op.execute("DROP TABLE md_corporate_action_filing")
