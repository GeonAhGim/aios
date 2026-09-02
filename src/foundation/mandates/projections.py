"""MandateStatusView 조립 — 75번 §4 GET 요약 화면용.

71번 §4 "read model may lag" — FND-01과 동일하게 지금은 프로젝션 워커 없이
같은 DB를 직접 읽으므로 지연이 없지만, `as_of`는 항상 포함한다(108번 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.mandates.application.create_draft_mandate import revision_to_view
from src.foundation.mandates.contracts.v1 import MandateRevisionView
from src.foundation.mandates.ports.repository import MandateRepository


class MandateStatusView:
    def __init__(
        self,
        tenant_id: UUID,
        active_revision: MandateRevisionView | None,
        pending_revision: MandateRevisionView | None,
        as_of: datetime,
    ) -> None:
        self.tenant_id = tenant_id
        self.active_revision = active_revision
        self.pending_revision = pending_revision
        self.as_of = as_of


async def build_mandate_status_view(repo: MandateRepository, tenant_id: UUID) -> MandateStatusView:
    mandate = await repo.get_mandate(tenant_id)
    if mandate is None:
        return MandateStatusView(
            tenant_id=tenant_id,
            active_revision=None,
            pending_revision=None,
            as_of=datetime.now(timezone.utc),
        )

    active = await repo.get_active_revision(mandate.id)
    revisions = await repo.list_revisions(mandate.id)
    pending = next(
        (r for r in revisions if r.state.value in ("DRAFT", "PROPOSED")),
        None,
    )
    return MandateStatusView(
        tenant_id=tenant_id,
        active_revision=revision_to_view(active) if active is not None else None,
        pending_revision=revision_to_view(pending) if pending is not None else None,
        as_of=datetime.now(timezone.utc),
    )
