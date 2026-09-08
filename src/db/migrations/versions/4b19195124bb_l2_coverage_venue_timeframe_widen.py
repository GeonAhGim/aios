"""RD-19 — coverage_spans venue/timeframe CHECK expansion (crypto L2 self-built collector).

Revision ID: 4b19195124bb
Revises: 3819cf8a5373
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3/D5, docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 (RD-19
is self-assigned — it isn't in the §9 table in the doc, but is
foreshadowed in §10 as "RD-19~"; task-1766 decision).

The `venue`/`timeframe` CHECK constraints on `coverage_spans` (DC-8,
9049e2b6b0b7) are pinned to a snapshot of the `Venue`/`Timeframe` enums
taken at migration-authoring time (`_sql_enum_members` — it does not
re-read the live enum every time). This swaps in new value lists for
just those two CHECKs so the DB doesn't reject the newly added
`Venue.{BINANCE,BYBIT,OKX,UPBIT}`/`Timeframe.L2` from `contracts/v1.py`
— the remaining columns, the EXCLUDE constraint, and the `entitlements`
table are out of scope for this leaf and are left untouched.

`entitlements` has the same `Venue`/`Timeframe` CHECK, but this leaf
leaves it alone since the L2 collection session doesn't write to that
table (only to coverage_spans) — a separate leaf will expand it if that
becomes necessary.
"""
from collections.abc import Sequence

from alembic import op

from src.foundation.market_data.contracts.v1 import Timeframe, Venue

revision: str = "4b19195124bb"
down_revision: str | Sequence[str] | None = "3819cf8a5373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VENUES = ("BITGET", "KIS_KRX", "KIS_US")
_OLD_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


def _sql_members(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_venue_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_venue_check "
        f"CHECK (venue IN ({_sql_members(tuple(m.value for m in Venue))}))"
    )
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_timeframe_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_timeframe_check "
        f"CHECK (timeframe IN ({_sql_members(tuple(m.value for m in Timeframe))}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_timeframe_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_timeframe_check "
        f"CHECK (timeframe IN ({_sql_members(_OLD_TIMEFRAMES)}))"
    )
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_venue_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_venue_check "
        f"CHECK (venue IN ({_sql_members(_OLD_VENUES)}))"
    )
