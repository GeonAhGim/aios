"""Shared fixtures/helpers for the paper_control lifecycle integration tests
(`test_paper_deployment_lifecycle.py`, `test_paper_deployment_idempotency.py`,
`test_paper_deployment_safety.py`) — 실제 dev DB 대상."""
from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.application.request_deployment import request_deployment
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresPaperControlRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


@pytest.fixture
def connection_repo(pool):
    return PostgresConnectionRepository(pool)


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


async def tenant_with_mandate(pool, mandate_repo, trust_repo):
    tenant_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)
    return tenant_id


async def request(repo, mandate_repo, tenant_id, *, key_suffix="", **overrides):
    defaults = dict(
        package_ref="pkg-ref-1",
        connection_id=None,
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-acct-1",
        endpoint_classification="SANDBOX",
    )
    defaults.update(overrides)
    return await request_deployment(
        repo,
        mandate_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        idempotency_key=f"req-{tenant_id}{key_suffix}",
        **defaults,
    )
