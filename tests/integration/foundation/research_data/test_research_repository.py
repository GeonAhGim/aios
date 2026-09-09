"""RD-4 — `research_items`/`research_sources` migration + `PostgresResearchRepository`
integration tests against a real DB (`TEST_DATABASE_URL`).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-4, §4 RD-A2.

DoD covered here (task-2463):
(a) migration upgrade -> downgrade -> upgrade round trip
(b) WORM: `aios_app` and the table owner both get rejected on UPDATE/DELETE
(c) a correction is a new row (revision_of), the original is untouched
(d) idempotent unique key: re-ingesting the same (source_id, external_id) never
    creates a second row and always returns the first row's id
(e) `research_sources.redistribution_scope` round-trips `link_only`
(f) cross-tenant lookup returns None (404 isomorphism), not another tenant's row
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from src.foundation.research_data.adapters.postgres_repository import (
    PostgresResearchRepository,
)
from src.foundation.research_data.contracts.v1 import (
    RedistributionPolicy,
    ResearchItem,
    SourceMeta,
)
from tests.integration.conftest import create_test_tenant

_MIGRATION_REVISION = "f5529244403f"


def _alembic_config() -> Config:
    return Config("alembic.ini")


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresResearchRepository:
    return PostgresResearchRepository(pool)


def _source(
    source_id: str, *, redistribution: RedistributionPolicy = "store_full"
) -> SourceMeta:
    return SourceMeta(
        source_id=source_id,
        publisher="Test Publisher",
        redistribution=redistribution,
        license_ref="https://example.invalid/license",
        rate_limit=60,
        coverage="2020-01-01~present",
    )


def _item(
    *, source_id: str, revision_of: UUID | None = None, known_at: datetime | None = None
) -> ResearchItem:
    now = known_at if known_at is not None else datetime.now(timezone.utc)
    return ResearchItem(
        item_id=uuid4(),
        source_id=source_id,
        kind="filing",
        published_at=now,
        known_at=now,
        instruments=("005930",),
        title="Test filing",
        body_ref=None,
        url="https://example.invalid/filing/1",
        language="ko",
        hash="a" * 64,
        revision_of=revision_of,
    )


async def _seed_source(
    repo: PostgresResearchRepository, *, redistribution: RedistributionPolicy = "store_full"
) -> str:
    source_id = f"src-{uuid4().hex[:12]}"
    await repo.upsert_source(_source(source_id, redistribution=redistribution))
    return source_id


async def test_migration_round_trip_upgrade_downgrade_upgrade(pool: asyncpg.Pool) -> None:
    cfg = _alembic_config()

    assert await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('research_items', 'research_sources', "
        "'research_item_instruments')"
    ) == 3

    await asyncio.to_thread(command.downgrade, cfg, "-1")
    remaining = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('research_items', 'research_sources', "
        "'research_item_instruments')"
    )
    assert remaining == 0

    await asyncio.to_thread(command.upgrade, cfg, _MIGRATION_REVISION)
    restored = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('research_items', 'research_sources', "
        "'research_item_instruments')"
    )
    assert restored == 3


async def test_aios_app_cannot_update_research_items(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    item = _item(source_id=source_id)
    item_id = await repo.append_item(tenant_id, item, external_id="ext-1")

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE research_items SET title = 'tampered' WHERE item_id = $1", item_id
            )
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


async def test_aios_app_cannot_delete_research_items(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    item = _item(source_id=source_id)
    item_id = await repo.append_item(tenant_id, item, external_id="ext-1")

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("DELETE FROM research_items WHERE item_id = $1", item_id)
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


async def test_worm_trigger_blocks_table_owner_update(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    """`pool` connects with no `SET ROLE` and owns `research_items` (it ran the
    migration) -- REVOKE never restricts an owner, so a rejection here proves the
    trigger itself, not the REVOKE, is doing the blocking (I7)."""
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    item = _item(source_id=source_id)
    item_id = await repo.append_item(tenant_id, item, external_id="ext-1")

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE research_items SET title = 'tampered' WHERE item_id = $1", item_id
            )


async def test_worm_trigger_blocks_table_owner_delete(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    item = _item(source_id=source_id)
    item_id = await repo.append_item(tenant_id, item, external_id="ext-1")

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM research_items WHERE item_id = $1", item_id)


async def test_correction_is_a_new_row_and_original_is_unchanged(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    original = _item(
        source_id=source_id, known_at=datetime(2026, 3, 1, tzinfo=timezone.utc)
    )
    original_id = await repo.append_item(tenant_id, original, external_id="ext-orig")

    correction = _item(
        source_id=source_id,
        revision_of=original_id,
        known_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
    )
    correction_id = await repo.append_item(tenant_id, correction, external_id="ext-correction")

    assert correction_id != original_id

    got_original = await repo.get_item(tenant_id, original_id)
    got_correction = await repo.get_item(tenant_id, correction_id)

    assert got_original is not None
    assert got_original.title == original.title
    assert got_original.revision_of is None

    assert got_correction is not None
    assert got_correction.revision_of == original_id


async def test_append_item_is_idempotent_on_source_and_external_id(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    tenant_id = await create_test_tenant(pool)
    item = _item(source_id=source_id)

    first_id = await repo.append_item(tenant_id, item, external_id="ext-replay")
    count_after_first = await pool.fetchval(
        "SELECT count(*) FROM research_items WHERE tenant_id = $1 AND source_id = $2 "
        "AND external_id = $3",
        tenant_id,
        source_id,
        "ext-replay",
    )
    assert count_after_first == 1

    replay_item = _item(source_id=source_id)  # a fresh item_id, same (source, external_id)
    second_id = await repo.append_item(tenant_id, replay_item, external_id="ext-replay")
    count_after_second = await pool.fetchval(
        "SELECT count(*) FROM research_items WHERE tenant_id = $1 AND source_id = $2 "
        "AND external_id = $3",
        tenant_id,
        source_id,
        "ext-replay",
    )

    assert count_after_second == 1
    assert second_id == first_id


async def test_source_redistribution_scope_round_trips_link_only(
    repo: PostgresResearchRepository,
) -> None:
    source_id = f"src-{uuid4().hex[:12]}"
    await repo.upsert_source(_source(source_id, redistribution="link_only"))

    got = await repo.get_source(source_id)

    assert got is not None
    assert got.redistribution == "link_only"


async def test_get_item_across_tenant_returns_none(
    pool: asyncpg.Pool, repo: PostgresResearchRepository
) -> None:
    source_id = await _seed_source(repo)
    owner_tenant = await create_test_tenant(pool)
    other_tenant = await create_test_tenant(pool)
    item = _item(source_id=source_id)
    item_id = await repo.append_item(owner_tenant, item, external_id="ext-cross-tenant")

    got_by_owner = await repo.get_item(owner_tenant, item_id)
    got_by_other = await repo.get_item(other_tenant, item_id)

    assert got_by_owner is not None
    assert got_by_other is None
