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

import asyncio
import os
import time
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
from src.foundation.ai.gateway.domain.token_rules import Scope, ScopeEscalationError, TokenRuleError
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


async def test_issue_rejects_empty_scopes(token_repo: PostgresAgentTokenRepository) -> None:
    """negative: 빈 스코프 집합은 토큰 발급을 거부해야 함."""
    with pytest.raises(TokenRuleError, match="at least one scope"):
        await issue(token_repo, tenant_id=UUID(int=1), scopes=frozenset())


async def test_issue_rejects_invalid_scope_value(token_repo: PostgresAgentTokenRepository) -> None:
    """negative: Scope enum에 없는 문자열은 예외를 일으켜야 함."""
    with pytest.raises(ScopeEscalationError):
        await issue(token_repo, tenant_id=UUID(int=2), scopes=frozenset({"BOGUS"}))  # type: ignore[arg-type]


async def test_issue_rejects_scope_escalation_beyond_universe(
    token_repo: PostgresAgentTokenRepository,
) -> None:
    """negative: grantable(ALL_SCOPES) 범위를 벗어난 스코프 조합도 거부되어야 함."""

    fake_scope = object()
    with pytest.raises(ScopeEscalationError):
        await issue(
            token_repo,
            tenant_id=UUID(int=3),
            scopes=frozenset({Scope.READ, fake_scope}),  # type: ignore[arg-type]
        )


async def test_issue_returns_valid_token(token_repo: PostgresAgentTokenRepository) -> None:
    """positive: issue()가 반환한 토큰의 필드가 유효한지 확인."""
    scopes = frozenset({Scope.READ, Scope.PROPOSE})
    issued = await issue(token_repo, tenant_id=UUID(int=4), scopes=scopes, ttl=timedelta(hours=2))
    assert issued.secret
    assert isinstance(issued.secret, str)
    assert len(issued.secret) > 0
    assert issued.token.expires_at > NOW
    assert issued.token.scopes == scopes


async def test_issue_increments_token_id(token_repo: PostgresAgentTokenRepository) -> None:
    """서로 다른 tenant로 연속 issue하면 token_id가 매번 고유해야 함."""
    t1 = await issue(token_repo, tenant_id=UUID(int=5), scopes=frozenset({Scope.READ}))
    t2 = await issue(token_repo, tenant_id=UUID(int=6), scopes=frozenset({Scope.READ}))
    assert t2.token.token_id != t1.token.token_id
    assert isinstance(t1.token.token_id, UUID)
    assert isinstance(t2.token.token_id, UUID)


async def test_issue_repository_failure_raises_exception(
    token_repo: PostgresAgentTokenRepository,
) -> None:
    """실패주입: repo.insert_token이 Exception을 raise하면 issue도 그대로 전파."""
    original_insert = token_repo.insert_token  # type: ignore[attr-defined]

    async def broken_insert(*args, **kwargs):
        raise RuntimeError("DB 연결 실패")

    token_repo.insert_token = broken_insert  # type: ignore[attr-defined]
    try:
        with pytest.raises(RuntimeError, match="DB 연결 실패"):
            await issue(token_repo, tenant_id=UUID(int=7), scopes=frozenset({Scope.READ}))
    finally:
        token_repo.insert_token = original_insert  # type: ignore[attr-defined]


async def test_issue_performance_under_load(token_repo: PostgresAgentTokenRepository) -> None:
    """성능: 50회 issue 호출이 5초 미만이어야 함."""
    start = time.perf_counter()
    tasks = [
        issue(token_repo, tenant_id=UUID(int=100 + i), scopes=frozenset({Scope.READ}))
        for i in range(50)
    ]
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"50회 issue 호출에 {elapsed:.3f}초 -- 예산 5초 초과"
