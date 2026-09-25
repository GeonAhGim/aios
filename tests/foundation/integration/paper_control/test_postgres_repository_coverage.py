"""TEST-cov task-4620 — src/foundation/paper_control/adapters/postgres_repository.py
0% -> coverage. 실제 dev DB 대상(다른 paper_control 통합테스트와 동일 conftest).

DoD: negative test >=3, failure-injection >=1, docs/design/INVARIANTS.md 위반 없음
(105번 표준 conditional_update/increment_fence의 fail-closed 재확인일 뿐, 새 불변식
추가는 없다)."""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
    PaperOrderIntent,
)
from tests.foundation.integration.paper_control.conftest import request, tenant_with_mandate
from tests.integration.conftest import create_test_tenant


def _deployment(
    *, tenant_id, mandate_revision_id, idempotency_key: str, digest: str = "digest-1"
) -> PaperDeployment:
    return PaperDeployment(
        id=uuid4(),
        tenant_id=tenant_id,
        connection_id=None,
        package_ref="pkg-ref-cov",
        mandate_revision_id=mandate_revision_id,
        provenance=AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref="sandbox-acct-cov",
        ),
        state=DeploymentState.REQUESTED,
        fence_token=0,
        request_idempotency_key=idempotency_key,
        request_digest=digest,
    )


async def _active_revision_id(mandate_repo, tenant_id):
    mandate = await mandate_repo.get_mandate(tenant_id)
    assert mandate is not None and mandate.active_revision_id is not None
    return mandate.active_revision_id


# --- negative: unknown id/key lookups return None instead of raising ---


async def test_get_deployment_returns_none_for_unknown_id(repo):
    assert await repo.get_deployment(uuid4()) is None


async def test_get_deployment_by_request_key_returns_none_for_unknown_key(
    pool, repo, mandate_repo, trust_repo
):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    assert await repo.get_deployment_by_request_key(tenant_id, "no-such-key") is None


async def test_get_command_by_idempotency_key_returns_none_for_unknown_key(repo):
    assert await repo.get_command_by_idempotency_key(uuid4(), "no-such-key") is None


async def test_list_deployments_empty_for_tenant_without_deployments(pool, repo):
    tenant_id = await create_test_tenant(pool)
    assert await repo.list_deployments(tenant_id) == []


# --- negative: conditional-write guards reject a stale expected_state (105 표준, fail-closed) ---


async def test_transition_deployment_state_wrong_expected_state_raises(
    pool, repo, mandate_repo, trust_repo
):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-transition")
    assert deployment.state.value == "READY"

    with pytest.raises(ConcurrencyConflictError):
        await repo.transition_deployment_state(
            deployment.id, expected_state="RUNNING", new_state="STOPPED"
        )
    # state untouched by the rejected write
    unchanged = await repo.get_deployment(deployment.id)
    assert unchanged is not None and unchanged.state.value == "READY"


async def test_increment_fence_wrong_expected_state_raises(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-fence")

    with pytest.raises(ConcurrencyConflictError):
        await repo.increment_fence(
            deployment.id, expected_state="RUNNING", new_state="PAUSED"
        )
    unchanged = await repo.get_deployment(deployment.id)
    assert unchanged is not None and unchanged.fence_token == 0


# --- positive: insert/read round-trips not covered by the higher-level lifecycle tests ---


async def test_insert_deployment_duplicate_idempotency_key_returns_existing_row(
    pool, repo, mandate_repo, trust_repo
):
    """PAP-006 race path — ON CONFLICT DO NOTHING + re-read (postgres_repository.py
    insert_deployment, "Lost the race" branch). request_deployment() itself
    short-circuits via get_deployment_by_request_key before ever calling
    insert_deployment twice, so this exercises the repository directly to reach
    the branch the application layer never triggers."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    revision_id = await _active_revision_id(mandate_repo, tenant_id)

    first = await repo.insert_deployment(
        _deployment(tenant_id=tenant_id, mandate_revision_id=revision_id, idempotency_key="dup-key")
    )
    second = await repo.insert_deployment(
        _deployment(tenant_id=tenant_id, mandate_revision_id=revision_id, idempotency_key="dup-key")
    )
    assert first.id == second.id
    assert first.request_idempotency_key == second.request_idempotency_key == "dup-key"


async def test_insert_command_and_get_by_idempotency_key(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-command")

    inserted = await repo.insert_command(
        deployment_id=deployment.id,
        idempotency_key="cmd-key-1",
        command_type=CommandType.START,
        actor_subject_id=tenant_id,
        outcome=CommandOutcome.ACCEPTED,
        detail="ok",
    )
    assert inserted.command_type == CommandType.START
    assert inserted.outcome == CommandOutcome.ACCEPTED

    fetched = await repo.get_command_by_idempotency_key(deployment.id, "cmd-key-1")
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.detail == "ok"


async def test_insert_order_intent(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-intent")

    intent = await repo.insert_order_intent(
        PaperOrderIntent(
            id=uuid4(),
            deployment_id=deployment.id,
            sequence=1,
            fence_token_at_submit=0,
            state="PENDING",
        )
    )
    assert intent.deployment_id == deployment.id
    assert intent.sequence == 1
    assert intent.fence_token_at_submit == 0
    assert intent.state == "PENDING"


async def test_list_running_deployments_excludes_ready_state(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-running")
    assert deployment.state.value == "READY"

    running_ids = [d.id for d in await repo.list_running_deployments()]
    assert deployment.id not in running_ids


# --- failure injection: pool-level errors propagate instead of being swallowed (fail-closed) ---


async def test_get_deployment_propagates_pool_connection_error(repo, monkeypatch):
    class _BoomPool:
        def acquire(self):
            raise asyncpg.PostgresConnectionError("boom")

    monkeypatch.setattr(repo, "_pool", _BoomPool())
    with pytest.raises(asyncpg.PostgresConnectionError):
        await repo.get_deployment(uuid4())
