"""Shared fixtures for `tests/api/mcp/` -- AI-15.

`pool`/`token_repo` mirror
[[tests/foundation/integration/ai/gateway/test_token_lifecycle.py]]'s own
fixtures exactly (real `TEST_DATABASE_URL`, `os.environ["DATABASE_URL"]` is
already remapped by the top-level `tests/conftest.py`).

`client`/`mandate_repo`/`trust_repo`/`coverage_repo`/`instrument_repo`/
`experiment_repo` moved here from `test_tools_propose_paper.py` (task-10072
DEEPEN split) so the sibling `test_tools_propose_paper_reliability.py` can
reuse them without duplicating fixture bodies -- pytest fixtures defined in
one test module are not visible to another module, only to conftest.py.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.mcp.server import create_mcp_app
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.issue_token import IssuedToken, issue_token
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository

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


@pytest.fixture
def coverage_repo(pool: asyncpg.Pool) -> PostgresCoverageRepository:
    return PostgresCoverageRepository(pool)


@pytest.fixture
def instrument_repo(pool: asyncpg.Pool) -> PostgresInstrumentRepository:
    return PostgresInstrumentRepository(pool)


@pytest.fixture
def mandate_repo(pool: asyncpg.Pool) -> PostgresMandateRepository:
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool: asyncpg.Pool) -> PostgresTrustRepository:
    return PostgresTrustRepository(pool)


@pytest.fixture
def experiment_repo(pool: asyncpg.Pool) -> PostgresExperimentRepository:
    return PostgresExperimentRepository(pool)


@pytest.fixture
async def client(pool: asyncpg.Pool) -> AsyncIterator[AsyncClient]:
    app = create_mcp_app(pool)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mcp.test") as c:
        yield c


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


# ---------------------------------------------------------------------------
# Negative / failure-injection / performance tests for conftest helpers
# ---------------------------------------------------------------------------
