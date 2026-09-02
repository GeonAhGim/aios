"""SafetyControlListView 조립 — 78번 §4 사용자 Control Center 목록용.

71번 §4 "read model may lag" — 다른 FND 컨텍스트와 동일하게 지금은 프로젝션
워커 없이 같은 DB를 직접 읽으므로 지연이 없지만, `as_of`는 항상
포함한다(108번 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.risk_gate.application.activate_safety_control import control_to_view
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.ports.repository import RiskGateRepository


class SafetyControlListView:
    def __init__(self, controls: list[SafetyControlView], as_of: datetime) -> None:
        self.controls = controls
        self.as_of = as_of


async def build_safety_control_list_view(
    repo: RiskGateRepository, tenant_id: UUID
) -> SafetyControlListView:
    # 레드팀 #2026-09-02-27 — 운영자 목록에는 특정 provider 하나로 좁히지
    # 않고 걸려있는 PROVIDER 통제 전부를 보여줘야 한다.
    controls = await repo.list_active_controls(tenant_id=tenant_id, include_all_providers=True)
    return SafetyControlListView(
        controls=[control_to_view(c) for c in controls],
        as_of=datetime.now(timezone.utc),
    )
