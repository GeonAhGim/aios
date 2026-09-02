"""SubmitPaperIntent 커맨드 — 실제 tick 워크플로(스케줄러)는 이 리프에
없다(71번 §1 FROZEN 영역과 무관한 새 개념이지만, 그 자체가 아직 미구현 —
마이그레이션 docstring 참조). 이 함수는 미래의 스케줄러 또는 수동
호출자가 "지금 이 fence로 하나의 주문 의도를 제출해도 되는가"를 확인하는
지점만 제공한다.

Spec: AIOSproject 77번 §3 "Every tick ... verifies current state/fence
immediately before intent and immediately before adapter call. Superseded
fence token means no-op/audit, never late order submission." — 이 함수는
그 "즉시 확인"을 하나의 원자적 UPDATE(increment 없이 상태/토큰만 조건부
확인)로 구현한다.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from src.foundation.paper_control.domain.models import DeploymentState, PaperOrderIntent
from src.foundation.paper_control.ports.paper_adapter import (
    PaperExecutionAdapter,
    PaperExecutionContext,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository


class DeploymentNotFoundError(Exception):
    pass


class FenceSupersededError(Exception):
    """PAP-004 — 이 fence로는 더 이상 제출할 수 없다(그 사이 pause/stop이
    fence를 이미 올렸다). 늦은 주문 제출이 아니라 no-op이다."""


async def submit_paper_intent(
    repo: PaperControlRepository,
    adapter: PaperExecutionAdapter,
    *,
    deployment_id: UUID,
    expected_fence_token: int,
    sequence: int,
) -> PaperOrderIntent:
    deployment = await repo.get_deployment(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError(str(deployment_id))

    # 77번 §3 "verifies ... immediately before intent" — adapter를 부르기
    # 전에 먼저 확인한다.
    fence_stale = deployment.fence_token != expected_fence_token
    if deployment.state != DeploymentState.RUNNING or fence_stale:
        raise FenceSupersededError(
            f"deployment.id={deployment_id}: fence {expected_fence_token}는 더 이상 "
            f"유효하지 않습니다(현재 상태={deployment.state.value}, "
            f"현재 fence={deployment.fence_token})."
        )

    context = PaperExecutionContext(
        deployment_id=str(deployment_id), provenance=deployment.provenance
    )
    ack = await adapter.submit_paper_intent(context, sequence)

    # "immediately before adapter call" 재확인 — adapter 호출 자체가
    # 네트워크 왕복이라 그 사이 pause/stop이 커밋됐을 수 있다.
    fresh = await repo.get_deployment(deployment_id)
    if (
        fresh is None
        or fresh.state != DeploymentState.RUNNING
        or fresh.fence_token != expected_fence_token
    ):
        await adapter.cancel_paper_order(context, ack.provider_order_ref)
        raise FenceSupersededError(
            f"deployment.id={deployment_id}: adapter 응답 도착 전에 fence가 superseded됐습니다 "
            "— 즉시 취소했습니다."
        )

    return await repo.insert_order_intent(
        PaperOrderIntent(
            id=uuid4(),
            deployment_id=deployment_id,
            sequence=sequence,
            fence_token_at_submit=expected_fence_token,
            state="SUBMITTED",
        )
    )
