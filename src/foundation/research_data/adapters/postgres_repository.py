"""RD-4 — asyncpg implementation of the `research_items`/`research_sources` store.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.2
`adapters/postgres_repository.py`, §4 RD-A2, §9 RD-4. Migration
`f5529244403f` is the schema this adapter talks to.

`append_item` is the RD-4 idempotency boundary (§3 "re-collection is the same row"):
the unique key is `(tenant_id, source_id, external_id)` — `external_id`
is the source's own document id (e.g. a DART receipt number), not part
of RD-2's `ResearchItem` contract, so it is a required parameter here
rather than a contract field. A second `append_item` call with the same
`(tenant_id, source_id, external_id)` never inserts a new row; it always
returns the `item_id` of the row already there, even when the caller
passed a freshly generated `item.item_id` for that call — the first
writer's id wins. A correction is a structurally different call (a new
`external_id`, with `revision_of` pointing at the original's `item_id`),
so it always inserts a new row and the original is never touched
(`research_items` is WORM — RD-A2 — enforced by migration
`f5529244403f`'s reuse of L0-3 `worm_sql()`, not by this adapter).

`get_item` takes `tenant_id` as a required filter, never an optional
scope: a wrong id and an id owned by another tenant both come back as
`None` (404 isomorphism, same principle as
`src/foundation/entities/adapters/_legal_entity_repository.py`).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta

__all__ = ["PostgresResearchRepository"]


def _row_to_item(row: asyncpg.Record) -> ResearchItem:
    return ResearchItem(
        item_id=row["item_id"],
        source_id=row["source_id"],
        kind=row["kind"],
        published_at=row["published_at"],
        known_at=row["known_at"],
        instruments=tuple(row["instruments"]),
        title=row["title"],
        body_ref=row["body_ref"],
        url=row["url"],
        language=row["language"],
        hash=row["hash"],
        revision_of=row["revision_of"],
    )


def _row_to_source(row: asyncpg.Record) -> SourceMeta:
    return SourceMeta(
        source_id=row["source_id"],
        publisher=row["publisher"],
        redistribution=row["redistribution_scope"],
        license_ref=row["license_ref"],
        rate_limit=row["rate_limit"],
        coverage=row["coverage"],
    )


class PostgresResearchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_source(self, source: SourceMeta) -> SourceMeta:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO research_sources "
                "(source_id, publisher, redistribution_scope, license_ref, rate_limit, coverage) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (source_id) DO UPDATE SET "
                "publisher = EXCLUDED.publisher, "
                "redistribution_scope = EXCLUDED.redistribution_scope, "
                "license_ref = EXCLUDED.license_ref, "
                "rate_limit = EXCLUDED.rate_limit, "
                "coverage = EXCLUDED.coverage "
                "RETURNING *",
                source.source_id,
                source.publisher,
                source.redistribution,
                source.license_ref,
                source.rate_limit,
                source.coverage,
            )
        if row is None:
            raise RuntimeError(f"upsert_source({source.source_id!r}) returned no row")
        return _row_to_source(row)

    async def get_source(self, source_id: str) -> SourceMeta | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research_sources WHERE source_id = $1", source_id
            )
        return _row_to_source(row) if row is not None else None

    async def append_item(
        self, tenant_id: UUID, item: ResearchItem, *, external_id: str
    ) -> UUID:
        async with self._pool.acquire() as conn:
            inserted = await conn.fetchrow(
                "INSERT INTO research_items "
                "(item_id, tenant_id, source_id, external_id, kind, published_at, known_at, "
                " instruments, title, body_ref, url, language, hash, revision_of) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) "
                "ON CONFLICT (tenant_id, source_id, external_id) DO NOTHING "
                "RETURNING item_id",
                item.item_id,
                tenant_id,
                item.source_id,
                external_id,
                item.kind,
                item.published_at,
                item.known_at,
                list(item.instruments),
                item.title,
                item.body_ref,
                item.url,
                item.language,
                item.hash,
                item.revision_of,
            )
            if inserted is not None:
                return UUID(str(inserted["item_id"]))

            existing = await conn.fetchrow(
                "SELECT item_id FROM research_items "
                "WHERE tenant_id = $1 AND source_id = $2 AND external_id = $3",
                tenant_id,
                item.source_id,
                external_id,
            )
        if existing is None:
            raise RuntimeError(
                f"append_item: conflicting row for (tenant_id={tenant_id}, "
                f"source_id={item.source_id!r}, external_id={external_id!r}) vanished"
            )
        return UUID(str(existing["item_id"]))

    async def get_item(self, tenant_id: UUID, item_id: UUID) -> ResearchItem | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM research_items WHERE item_id = $1 AND tenant_id = $2",
                item_id,
                tenant_id,
            )
        return _row_to_item(row) if row is not None else None
