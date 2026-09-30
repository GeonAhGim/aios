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
    stop_deployment,
)
from src.foundation.paper_control.application.request_deployment import request_deployment
from src.foundation.paper_control.projections import build_deployment_list_view
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
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
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
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _owned_deployment(pool, repo, mandate_repo, trust_repo, tenant_a)

    view_b = await build_deployment_list_view(repo, tenant_b)

    assert view_b.deployments == []


async def test_pausing_nonexistent_deployment_raises_not_found(pool, repo):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(DeploymentNotFoundError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=uuid4(),
            idempotency_key="ghost",
        )


async def test_cannot_stop_another_tenants_deployment(pool, repo, mandate_repo, trust_repo):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    deployment = await _owned_deployment(pool, repo, mandate_repo, trust_repo, owner_id)

    with pytest.raises(CrossTenantDeploymentAccessError):
        await stop_deployment(
            repo,
            tenant_id=attacker_id,
            actor_subject_id=attacker_id,
            deployment_id=deployment.id,
            idempotency_key="attacker-stop",
        )

    still_ready = await repo.get_deployment(deployment.id)
    assert still_ready.state.value == "READY"


async def test_pause_fails_closed_when_repository_lookup_raises(
    monkeypatch, pool, repo, mandate_repo, trust_repo
):
    """Dependency 예외가 나도 tenant 격리 체크를 우회해 상태를 바꾸면 안 된다(fail-closed)."""
    owner_id = await create_test_tenant(pool)
    deployment = await _owned_deployment(pool, repo, mandate_repo, trust_repo, owner_id)

    async def _boom(_deployment_id):
        raise RuntimeError("simulated repository outage")

    monkeypatch.setattr(repo, "get_deployment", _boom)

    with pytest.raises(RuntimeError, match="simulated repository outage"):
        await pause_deployment(
            repo,
            tenant_id=owner_id,
            actor_subject_id=owner_id,
            deployment_id=deployment.id,
            idempotency_key="owner-pause-during-outage",
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state FROM paper_deployment WHERE id = $1", deployment.id)
    assert row["state"] == "READY"


async def test_stop_fails_closed_when_increment_fence_raises(
    monkeypatch, pool, repo, mandate_repo, trust_repo
):
    """stop_deployment도 dependency 예외 시 상태를 바꾸지 않는다(fail-closed 일관성)."""
    owner_id = await create_test_tenant(pool)
    deployment = await _owned_deployment(pool, repo, mandate_repo, trust_repo, owner_id)

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated fence increment outage")

    monkeypatch.setattr(repo, "increment_fence", _boom)

    with pytest.raises(RuntimeError, match="simulated fence increment outage"):
        await stop_deployment(
            repo,
            tenant_id=owner_id,
            actor_subject_id=owner_id,
            deployment_id=deployment.id,
            idempotency_key="owner-stop-during-outage",
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state FROM paper_deployment WHERE id = $1", deployment.id)
    assert row["state"] == "READY"


async def test_cannot_modify_deployment_with_unauthorized_actor(
    pool, repo, mandate_repo, trust_repo
):
    """같은 tenant이지만 mandate 권한이 없는 actor는 pause할 수 없다 (권한 불변식)."""
    from uuid import uuid4 as make_uuid

    tenant_id = await create_test_tenant(pool)
    authorized_actor = tenant_id
    unauthorized_actor = make_uuid()

    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)
    deployment = await request_deployment(
        repo,
        mandate_repo,
        tenant_id=tenant_id,
        actor_subject_id=authorized_actor,
        package_ref="pkg-ref-authz-test",
        connection_id=None,
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-acct",
        endpoint_classification="SANDBOX",
        idempotency_key=f"req-authz-{tenant_id}",
    )

    with pytest.raises(Exception):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=unauthorized_actor,
            deployment_id=deployment.id,
            idempotency_key="pause-by-unauthorized-actor",
        )


async def test_list_view_filters_correct_tenant_from_multiple_deployments(
    pool, repo, mandate_repo, trust_repo
):
    """여러 deployment가 있을 때 정확한 tenant만 조회한다 (성능 + 정확성 단언)."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    tenant_c = await create_test_tenant(pool)

    await _owned_deployment(pool, repo, mandate_repo, trust_repo, tenant_a)
    dep_b = await _owned_deployment(pool, repo, mandate_repo, trust_repo, tenant_b)
    await _owned_deployment(pool, repo, mandate_repo, trust_repo, tenant_c)

    view_b = await build_deployment_list_view(repo, tenant_b)

    assert len(view_b.deployments) == 1
    assert view_b.deployments[0].id == dep_b.id


async def test_idempotency_prevents_concurrent_deployments(pool, repo, mandate_repo, trust_repo):
    """같은 idempotency_key로 여러 배포를 요청하면 첫 번째만 성공한다 (원자성 단언)."""
    owner_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=owner_id)

    idempotency_key = f"req-conflict-{owner_id}"
    await request_deployment(
        repo,
        mandate_repo,
        tenant_id=owner_id,
        actor_subject_id=owner_id,
        package_ref="pkg-conflict-a",
        connection_id=None,
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-a",
        endpoint_classification="SANDBOX",
        idempotency_key=idempotency_key,
    )

    from src.foundation.paper_control.application.request_deployment import (
        IdempotencyKeyConflictError,
    )

    with pytest.raises(IdempotencyKeyConflictError):
        await request_deployment(
            repo,
            mandate_repo,
            tenant_id=owner_id,
            actor_subject_id=owner_id,
            package_ref="pkg-conflict-b",
            connection_id=None,
            adapter_type="fake-paper-v1",
            provider_sandbox_account_ref="sandbox-b",
            endpoint_classification="SANDBOX",
            idempotency_key=idempotency_key,
        )
