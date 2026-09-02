"""DeploymentListView 조립 — 77번 §5 Control Center 목록용.

71번 §4 "read model may lag" — 다른 FND 컨텍스트와 동일하게 지금은 프로젝션
워커 없이 같은 DB를 직접 읽으므로 지연이 없지만, `as_of`는 항상
포함한다(108번 §2).
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
