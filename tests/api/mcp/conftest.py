"""Shared fixtures for `tests/api/mcp/` -- AI-15.

`pool`/`token_repo` mirror
[[tests/foundation/integration/ai/gateway/test_token_lifecycle.py]]'s own
fixtures exactly (real `TEST_DATABASE_URL`, `os.environ["DATABASE_URL"]` is
already remapped by the top-level `tests/conftest.py`).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.issue_token import IssuedToken, issue_token
from src.foundation.ai.gateway.domain.token_rules import Scope

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def token_repo(pool: asyncpg.Pool) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


async def issue(
    repo: PostgresAgentTokenRepository,
    *,
    tenant_id: UUID,
    scopes: frozenset[Scope],
    ttl: timedelta = timedelta(hours=1),
    now: datetime = NOW,
) -> IssuedToken:
    return await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=scopes,
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=ttl,
        now=now,
    )
