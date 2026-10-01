"""FND-07 Paper Execution & Control 통합테스트 — request_deployment()
idempotency key 처리(중복 요청 재현, 충돌, FAILED replay)."""

from __future__ import annotations

import asyncio

import pytest

from src.foundation.paper_control.application.pause_deployment import pause_deployment
from src.foundation.paper_control.application.request_deployment import (
    IdempotencyKeyConflictError,
)
from src.foundation.paper_control.application.start_deployment import start_deployment
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from tests.foundation.integration.paper_control.conftest import request, tenant_with_mandate


async def test_duplicate_request_with_same_key_returns_existing_deployment(
    pool, repo, mandate_repo, trust_repo
):
    """PM 배정 ③ — 전수감사 발견 회귀. 이전에는 request_deployment()가 매번
    새 deployment_id를 만들어 같은 idempotency_key로 재시도해도 중복
    deployment가 생겼다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    first = await request(repo, mandate_repo, tenant_id, key_suffix="-dup-req")
    second = await request(repo, mandate_repo, tenant_id, key_suffix="-dup-req")
    assert first.id == second.id

    all_deployments = await repo.list_deployments(tenant_id)
    assert len(all_deployments) == 1


async def test_duplicate_request_key_with_different_body_is_conflict(
    pool, repo, mandate_repo, trust_repo
):
    """같은 키를 다른 요청에 재사용하면(클라이언트 버그) 예전 응답을 조용히
    재사용하는 대신 명시적으로 거부한다 — 진짜 idempotency는 "같은 요청의
    재시도"만 캐시해야 한다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    await request(repo, mandate_repo, tenant_id, key_suffix="-conflict-req")
    with pytest.raises(IdempotencyKeyConflictError):
        await request(
            repo,
            mandate_repo,
            tenant_id,
            key_suffix="-conflict-req",
            package_ref="pkg-ref-DIFFERENT",
        )


async def test_failed_request_replay_reraises_same_error_without_duplicating(
    pool, repo, mandate_repo, trust_repo
):
    """FAILED로 끝난 REQUEST도(PAP-002) 같은 키 재시도는 새 deployment를
    만들지 않고 같은 예외를 재현해야 한다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    with pytest.raises(InvalidProvenanceError):
        await request(
            repo,
            mandate_repo,
            tenant_id,
            key_suffix="-failed-req",
            endpoint_classification="LIVE_PRODUCTION",
        )
    with pytest.raises(InvalidProvenanceError):
        await request(
            repo,
            mandate_repo,
            tenant_id,
            key_suffix="-failed-req",
            endpoint_classification="LIVE_PRODUCTION",
        )

    all_deployments = await repo.list_deployments(tenant_id)
    assert len(all_deployments) == 1
    assert all_deployments[0].state.value == "FAILED"


async def test_concurrent_pause_with_same_idempotency_key_is_idempotent(
    pool, repo, risk_repo, mandate_repo, trust_repo, connection_repo
):
    """안정화 감사 A6-G5-4(task-10780) 재현 — 같은 idempotency_key로 두 PAUSE
    요청이 동시에(같은 RUNNING 배포에) 들어오면, 경합에서 이긴 쪽만 실제
    전이를 하고 진 쪽은 에러 대신 같은 결과를 그대로 받아야 한다(PAP-006
    "duplicate command is idempotent"가 동시 재시도에도 성립해야 한다)."""
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
        idempotency_key="start-concurrent-pause",
    )

    async def attempt():
        return await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-concurrent-same-key",
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures == [], f"동일 키 동시 PAUSE가 에러를 내면 안 된다: {failures}"

    views = [r for r in results if not isinstance(r, BaseException)]
    assert all(v.state.value == "PAUSED" for v in views)
    assert views[0].fence_token == views[1].fence_token == 1

    all_deployments = await repo.list_deployments(tenant_id)
    assert len(all_deployments) == 1
    assert all_deployments[0].fence_token == 1, "경합에서 진 쪽이 fence를 한 번 더 늘리면 안 된다"
