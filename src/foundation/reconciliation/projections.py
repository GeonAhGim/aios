"""ReconciliationStateListView 조립 — 80번 §3 Control Center 목록용.

71번 §4 "read model may lag" — 다른 FND 컨텍스트와 동일하게 지금은 프로젝션
워커 없이 같은 DB를 직접 읽으므로 지연이 없지만, `as_of`는 항상
포함한다(108번 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.reconciliation.application.resolve_reconciliation import state_to_view
from src.foundation.reconciliation.contracts.v1 import ReconciliationStateView
from src.foundation.reconciliation.ports.repository import ReconciliationRepository


class ReconciliationStateListView:
    def __init__(self, states: list[ReconciliationStateView], as_of: datetime) -> None:
        self.states = states
        self.as_of = as_of


async def build_reconciliation_state_list_view(
    repo: ReconciliationRepository, tenant_id: UUID
) -> ReconciliationStateListView:
    states = await repo.list_states(tenant_id)
    return ReconciliationStateListView(
        states=[state_to_view(s) for s in states],
        as_of=datetime.now(timezone.utc),
    )
