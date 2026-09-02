"""Paper Execution & Control adversarial 테스트 — 73번 TRU-006과 동일 원칙:
다른 tenant의 deployment를 조회/제어/참조할 수 없어야 한다."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.application.pause_deployment import (
    CrossTenantDeploymentAccessError,
    DeploymentNotFoundError,
    pause_deployment,
)
from src.foundation.paper_control.application.request_deployment import request_deployment
from src.foundation.paper_control.projections import build_deployment_list_view
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
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


async def _owned_deployment(pool, repo, mandate_repo, trust_repo, owner_id):
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=owner_id)
    return await request_deployment(
        repo,
        mandate_repo,
        tenant_id=owner_id,
        actor_subject_id=owner_id,
        package_ref="pkg-ref-owner",
        connection_id=None,
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-acct-owner",
        endpoint_classification="SANDBOX",
        idempotency_key=f"req-{owner_id}",
    )


async def test_cannot_pause_another_tenants_deployment(pool, repo, mandate_repo, trust_repo):
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    deployment = await _owned_deployment(pool, repo, mandate_repo, trust_repo, owner_id)

    with pytest.raises(CrossTenantDeploymentAccessError):
        await pause_deployment(
            repo,
            tenant_id=attacker_id,
            actor_subject_id=attacker_id,
            deployment_id=deployment.id,
            idempotency_key="attacker-pause",
        )

    still_ready = await repo.get_deployment(deployment.id)
    assert still_ready.state.value == "READY"


async def test_deployment_list_view_never_includes_another_tenants_deployment(
    pool, repo, mandate_repo, trust_repo
):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await _owned_deployment(pool, repo, mandate_repo, trust_repo, tenant_a)

    view_b = await build_deployment_list_view(repo, tenant_b)

    assert view_b.deployments == []


async def test_pausing_nonexistent_deployment_raises_not_found(pool, repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(DeploymentNotFoundError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=uuid4(),
            idempotency_key="ghost",
        )
