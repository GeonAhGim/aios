from __future__ import annotations

from uuid import UUID

from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.contracts.v1 import Autonomy, MandateRuleInput
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.trust.ports.repository import TrustRepository


async def activate_mandate_with_defaults(
    mandate_repo: MandateRepository, trust_repo: TrustRepository, *, tenant_id: UUID
) -> None:
    """risk_gate가 mandate_available=True/mandate_blocking=False를 받으려면
    tenant에 ACTIVE mandate가 있어야 한다 — 최초 활성화는 material change가
    아니라 재인증이 필요 없다(FND-02 activate_revision.py)."""
    draft = await create_draft_mandate(
        mandate_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        rules=MandateRuleInput(
            max_total_exposure_pct=80.0,
            max_single_instrument_pct=20.0,
            min_cash_buffer_pct=5.0,
            max_daily_loss_pct=3.0,
            allowed_autonomy=Autonomy.PAPER,
            forbidden_assets=[],
        ),
    )
    await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
