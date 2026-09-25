"""Paper Execution & Control repository port. The domain knows only this
Protocol; actual implementations (adapters/) remain opaque to it (71 §4)."""
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
        """All-tenant scope — the only use case for a GLOBAL kill switch to
        find deployments it must actually stop (77 §2 "risk/emergency PAUSE
        outranks START/RESUME"). Never used for other purposes."""
        ...

    async def get_deployment_by_request_key(
        self, tenant_id: UUID, request_idempotency_key: str
    ) -> PaperDeployment | None:
        """PAP-006 — Replays of REQUEST return the existing deployment instead
        of creating a new one."""
        ...

    async def insert_deployment(self, deployment: PaperDeployment) -> PaperDeployment:
        """When `deployment.request_idempotency_key` is set, a `(tenant_id,
        request_idempotency_key)` UNIQUE collision returns the existing row
        instead of creating a new one (implementation responsibility — ON
        CONFLICT DO NOTHING + re-query, same pattern as CON-006). The caller
        does not distinguish whether the returned row was just created by them
        or already existed — digest comparison is the application layer's
        responsibility."""
        ...

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        """PAP-006 "duplicate command is idempotent" — check this first and
        return the existing result as-is (the implementation must not
        re-evaluate)."""
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
        """State transition via conditional_update from the standard-105 pattern."""
        ...

    async def increment_fence(
        self, deployment_id: UUID, *, expected_state: str, new_state: str
    ) -> PaperDeployment:
        """77 §3 "Pause ... fence token increments" — dedicated to transitions
        that must invalidate in-flight work, such as pause/stop. Binds state
        transition and fence increment into a single atomic UPDATE (implementation
        responsibility)."""
        ...

    async def insert_order_intent(self, intent: PaperOrderIntent) -> PaperOrderIntent: ...
