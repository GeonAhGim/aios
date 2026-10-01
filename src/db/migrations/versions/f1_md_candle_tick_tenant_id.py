"""[health:audit_correction] F1(M) -- add tenant_id to md_candle/md_tick.

Revision ID: f1a9c6d3e8b2
Revises: d4e8f1a29c37

Spec: docs/audits/AUDIT_2026-10-01_data_ingest_replay.md §2 F1(M), task-10465
(found by task-10439). `md_ingest_batch`/`md_ingest_batch_tick` already carry
`tenant_id`, but `md_candle`/`md_tick` do not, so RLS cannot be applied to
either and the idempotency/partition key (venue, instrument_id, timeframe,
open_time or venue, instrument_id, trade_id, traded_at) alone cannot separate
tenants -- tenant A's ingested data could be exposed to tenant B's query.

NOT NULL is intentionally not enforced: `IngestCandlesCommand.tenant_id`
(contracts/v1.py:122, "tenant_id=None means platform-shared data") already
defines `None` as a legitimate "platform-shared data" value, not "unknown",
and `md_ingest_batch.tenant_id`/`md_ingest_batch_tick.tenant_id` have stayed
nullable since f6b25409405e (FA-0a batch B) for the same reason -- forcing
NOT NULL here would break that contract and make platform-shared data
unrepresentable (the task note's literal "enforce NOT NULL" conflicts with
this and is reported to the PM as a deviation). The FK target is `tenant(id)`,
not `users(user_id)`: f6b25409405e/94da854f522f already corrected every
market_data tenant_id FK from `users(user_id)` to `tenant(id)`, and
reintroducing the pre-correction pattern here would undo that fix.

Backfill: `md_candle` is WORM (append-only, see `_WORM_TABLES` in
`4a1d0c0de008`), so any UPDATE on an existing row is rejected by
`{table}_worm_guard_trg` (review-migration checklist #8 -- temporarily
dropping the trigger to work around this is also disallowed). So this
migration does not attempt to backfill `md_candle.tenant_id` from
`md_ingest_batch.tenant_id` via UPDATE. Rows written before this revision
keep `tenant_id = NULL` permanently (which correctly records "written before
this leaf"); only candles ingested after this revision get `tenant_id` filled,
via `ingest_candles.py` (task-10465) passing it through to
`CandleStore.upsert_batch` at INSERT time. `md_tick` is not WORM (absent from
the list above), so an UPDATE backfill is possible -- but it has no `batch_id`
column (partition UNIQUE constraint, task-450 note) to join against a batch,
so it is only backfilled to the sole tenant when the `tenant` table has
exactly one row (the only case where the tenant can be determined with
certainty). If there are zero or two-or-more tenants, which tenant is correct
cannot be known, so the column is left NULL instead of guessing
(fail-closed -- a wrong backfill is worse than no backfill).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a9c6d3e8b2"
down_revision: str | Sequence[str] | None = "d4e8f1a29c37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE md_candle ADD COLUMN tenant_id UUID REFERENCES tenant(id)")
    op.execute("ALTER TABLE md_tick ADD COLUMN tenant_id UUID REFERENCES tenant(id)")

    # md_candle is WORM, so an UPDATE backfill is impossible (see docstring above) -- skipped.
    op.execute(
        """
        UPDATE md_tick
        SET tenant_id = (SELECT id FROM tenant)
        WHERE (SELECT COUNT(*) FROM tenant) = 1
        """
    )

    op.execute(
        "CREATE INDEX idx_md_candle_tenant_id ON md_candle(tenant_id) WHERE tenant_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_md_tick_tenant_id ON md_tick(tenant_id) WHERE tenant_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_md_tick_tenant_id")
    op.execute("DROP INDEX IF EXISTS idx_md_candle_tenant_id")
    op.execute("ALTER TABLE md_tick DROP COLUMN tenant_id")
    op.execute("ALTER TABLE md_candle DROP COLUMN tenant_id")
