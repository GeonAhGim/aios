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

    async def list_running_deployments(self) -> list[PaperDeployment]:
        """전 tenant 대상 — GLOBAL 범위 kill switch가 실제로 정지시켜야 할
        대상을 찾는 용도 하나뿐이다(77번 §2 "risk/emergency PAUSE outranks
        START/RESUME"). 그 외 목적으로 쓰지 않는다."""
        ...

    async def get_deployment_by_request_key(
        self, tenant_id: UUID, request_idempotency_key: str
    ) -> PaperDeployment | None:
        """PAP-006 — REQUEST 재시도를 새 deployment 대신 기존 것으로 되돌린다."""
        ...

    async def insert_deployment(self, deployment: PaperDeployment) -> PaperDeployment:
        """`deployment.request_idempotency_key`가 채워져 있으면 `(tenant_id,
        request_idempotency_key)` UNIQUE 충돌 시 새로 만들지 않고 기존 행을
        그대로 반환한다(구현체 책임 — ON CONFLICT DO NOTHING + 재조회, CON-006과
        같은 패턴). 호출자는 반환된 행이 자신이 방금 만든 것인지 이미 있던
        것인지 구분하지 않는다 — digest 비교는 애플리케이션 레이어의 몫."""
        ...

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
