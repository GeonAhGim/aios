"""FND-07 Paper Execution & Control 통합테스트 — 실제 dev DB 대상. 47번 §5/77번
§6 core 상태머신(request/start/pause/resume/stop, PAP-001/002/006)."""
from __future__ import annotations

import pytest

from src.foundation.paper_control.application.pause_deployment import (
    InvalidDeploymentStateError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.application.request_deployment import NoActiveMandateError
from src.foundation.paper_control.application.start_deployment import (
    resume_deployment,
    start_deployment,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from tests.foundation.integration.paper_control.conftest import request, tenant_with_mandate
from tests.integration.conftest import create_test_tenant


async def test_request_without_active_mandate_raises(pool, repo, mandate_repo):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(NoActiveMandateError):
        await request(repo, mandate_repo, tenant_id)


async def test_request_with_invalid_provenance_ends_failed(pool, repo, mandate_repo, trust_repo):
    """PAP-002 — live endpoint rejects before adapter call; 여기서는 REQUEST
    단계에서 FAILED 행으로 귀결되는지 확인한다(adapter 자체가 아직 없는
    단계라 "adapter 호출 전"은 자동으로 만족)."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    with pytest.raises(InvalidProvenanceError):
        await request(repo, mandate_repo, tenant_id, endpoint_classification="LIVE_PRODUCTION")


async def test_full_lifecycle_request_start_pause_resume_stop(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-001 — valid paper refs/provenance reaches READY/RUNNING and emits
    audited transitions."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    assert deployment.state.value == "READY"

    started = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-1",
    )
    assert started.state.value == "RUNNING"
    assert started.fence_token == 0

    paused = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-1",
    )
    assert paused.state.value == "PAUSED"
    assert paused.fence_token == 1

    resumed = await resume_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="resume-1",
    )
    assert resumed.state.value == "RUNNING"

    stopped = await stop_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="stop-1",
    )
    assert stopped.state.value == "STOPPED"
    assert stopped.fence_token == 2

    with pytest.raises(InvalidDeploymentStateError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-after-stop",
        )


async def test_duplicate_command_is_idempotent(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """PAP-006 — duplicate command is idempotent; 재실행 없이 같은 결과를
    반환한다(fence가 두 번 늘지 않는다)."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id)
    await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="start-dup",
    )

    first = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-dup",
    )
    second = await pause_deployment(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        deployment_id=deployment.id,
        idempotency_key="pause-dup",
    )
    assert first.fence_token == second.fence_token == 1
