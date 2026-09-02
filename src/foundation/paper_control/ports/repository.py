"""Paper Execution & Control repository port. domain은 이 Protocol만 알고,
실제 구현(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.paper_control.domain.models import (
    CommandOutcome,
    CommandType,
    DeploymentCommand,
    PaperDeployment,
    PaperOrderIntent,
)


class PaperControlRepository(Protocol):
    async def get_deployment(self, deployment_id: UUID) -> PaperDeployment | None: ...

    async def list_deployments(self, tenant_id: UUID) -> list[PaperDeployment]: ...

    async def insert_deployment(self, deployment: PaperDeployment) -> PaperDeployment: ...

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        """PAP-006 "duplicate command is idempotent" — 먼저 이걸 확인해
        기존 결과를 그대로 반환한다(구현체가 새로 재평가하지 않는다)."""
        ...

    async def insert_command(
        self,
        *,
        deployment_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        actor_subject_id: UUID,
        outcome: CommandOutcome,
        detail: str | None,
    ) -> DeploymentCommand: ...

    async def transition_deployment_state(
        self,
        deployment_id: UUID,
        *,
        expected_state: str,
        new_state: str,
    ) -> PaperDeployment:
        """105번 표준의 conditional_update로 상태 전이."""
        ...

    async def increment_fence(
        self, deployment_id: UUID, *, expected_state: str, new_state: str
    ) -> PaperDeployment:
        """77번 §3 "Pause ... fence token increments" — pause/stop처럼 진행
        중인 작업을 무효화해야 하는 전이 전용. 상태 전이와 fence 증가를
        하나의 원자적 UPDATE로 묶는다(구현체 책임)."""
        ...

    async def insert_order_intent(self, intent: PaperOrderIntent) -> PaperOrderIntent: ...
