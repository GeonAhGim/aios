"""DeploymentListView assembly — for Control Center list (spec 77 §5).

71 §4 "read model may lag" — same as other FND contexts, currently reading
from the same DB directly without a projection worker, so there is no latency,
but `as_of` must always be included (spec 108 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.paper_control.application.request_deployment import deployment_to_view
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.ports.repository import PaperControlRepository


class DeploymentListView:
    def __init__(self, deployments: list[PaperDeploymentView], as_of: datetime) -> None:
        self.deployments = deployments
        self.as_of = as_of


async def build_deployment_list_view(
    repo: PaperControlRepository, tenant_id: UUID
) -> DeploymentListView:
    deployments = await repo.list_deployments(tenant_id)
    return DeploymentListView(
        deployments=[deployment_to_view(d) for d in deployments],
        as_of=datetime.now(timezone.utc),
    )
