"""Common integration test utilities for post-11.1 schemas.

After 11.1, user_id FKs span many tables. Integration tests that insert
rows into those tables must create a users row first (to avoid FK violations).
Each call creates a separate user with a unique email to maintain test isolation.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from tests.support.entities_seed import bootstrap_default_hierarchy


async def create_test_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
            f"test-{uuid4().hex}@example.com",
            "test-hash",
        )
    user_id: UUID = row["user_id"]
    return user_id


async def create_test_tenant(
    pool: asyncpg.Pool, *, bootstrap_default_hierarchy_rows: bool = True
) -> UUID:
    """Create a `users` row plus its PERSONAL `tenant` row (id == user_id).

    `f4a6b8c0d2e4` backfills PERSONAL tenants only for existing users, so
    newly created users have no corresponding `tenant` row without this helper.
    To insert rows into tables that FK `tenant(id)` (e.g. `legal_entity`),
    use the id produced by this helper.

    FA-0d-fix (task-771991202): by default the tenant's FA-1 default
    hierarchy (legal entity / fund / portfolio / sub-account, deterministic
    ids) is persisted too, through the same idempotent
    `ensure_default_hierarchy` the operational bootstrap uses -- every
    `pos_snapshot` write now carries `default_portfolio_id(tenant_id)` as a
    real FK, so a tenant without it cannot open a position. Pass
    `bootstrap_default_hierarchy_rows=False` only for tests that assert the
    un-bootstrapped branch or create the default-id rows themselves."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenant (id, kind) VALUES ($1, 'PERSONAL')", user_id
        )
    if bootstrap_default_hierarchy_rows:
        await bootstrap_default_hierarchy(pool, user_id)
    return user_id


class NoopEventBus:
    """NoopEventBus stub for FastAPI router integration tests — discards publishes immediately.

    The real lifespan in main.py uses InProcessEventBus + NotificationGateway(no-op
    sender), but if events are published in that state the gateway treats each as a
    send failure, triggering CRITICAL retries (up to 5, exponential backoff up to 31s,
    §5.5). The lifespan shutdown waits for `event_bus.stop()` which blocks until retries
    finish, causing every router test that publishes events to take tens of seconds.
    Replace with `app.dependency_overrides[get_event_bus] = lambda: NoopEventBus()`
    to verify "an event was published" without triggering the retry path."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.published.append((topic, payload))

    def subscribe(  # noqa: ANN001
        self,
        topic: str,
        handler: Callable[[str, dict[str, Any]], Awaitable[None]],
        *,
        criticality: str,
    ) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None
