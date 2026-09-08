"""AmendMandate 커맨드 — 현재 ACTIVE revision을 기준으로 새 PROPOSED revision을
append한다.

Spec: AIOSproject 45번 §3 (`AmendMandate` -> proposed revision),
docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-5.

CM-5: when `proposer_id`/`audit_repo` are supplied, this command records who
authored the proposal via the existing `foundation_audit_event` audit trail
(no new column — `mandate_revision` has no `proposer_id` field and this leaf
must not add one, per the CM-5 decision note). `activate_revision.py` reads
that same event back to enforce "author != approver" (CM-A3). Both
parameters default to `None` so this stays call-compatible with existing
callers that do not participate in that governance path (e.g. the very
first revision from `create_draft_mandate`, which likewise records no
proposer) — omitting them simply means no counterparty is recorded, so the
segregation-of-duty check in `activate_revision.py` has nothing to compare
against and passes through (same semantics as
`assert_actor_not_counterparty(..., counterparty_id=None, ...)`).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.mandates.application.create_draft_mandate import revision_to_view
from src.foundation.mandates.contracts.v1 import MandateRevisionView, MandateRuleInput
from src.foundation.mandates.domain.models import Autonomy as DomainAutonomy
from src.foundation.mandates.domain.models import MandateRevision as DomainRevision
from src.foundation.mandates.domain.models import MandateRevisionState
from src.foundation.mandates.domain.rules import compute_revision_hash, detect_material_change
from src.foundation.mandates.ports.repository import MandateRepository

# CM-5 — `activate_revision.py` looks up the most recent event with this
# action for a given `mandate_revision` aggregate_id to find its proposer.
# Single source of truth for both sides of the wiring (avoids a typo'd
# duplicate literal drifting between the two modules).
PROPOSED_ACTION = "mandate_revision_proposed"


class NoActiveMandateError(Exception):
    """75번 §1 "active mandate가 없으면 개정할 대상이 없다" — 최초 설정은
    create_draft_mandate + activate_revision을 쓴다."""


async def propose_amendment(
    repo: MandateRepository,
    *,
    tenant_id: UUID,
    rules: MandateRuleInput,
    proposer_id: UUID | None = None,
    audit_repo: AuditEventRepository | None = None,
) -> MandateRevisionView:
    mandate = await repo.get_mandate(tenant_id)
    if mandate is None:
        raise NoActiveMandateError(str(tenant_id))
    current_active = await repo.get_active_revision(mandate.id)
    if current_active is None:
        raise NoActiveMandateError(str(tenant_id))

    existing_revisions = await repo.list_revisions(mandate.id)
    next_revision_no = max(r.revision_no for r in existing_revisions) + 1

    proposed = DomainRevision(
        id=uuid4(),
        mandate_id=mandate.id,
        revision_no=next_revision_no,
        state=MandateRevisionState.PROPOSED,
        max_total_exposure_pct=rules.max_total_exposure_pct,
        max_single_instrument_pct=rules.max_single_instrument_pct,
        min_cash_buffer_pct=rules.min_cash_buffer_pct,
        max_daily_loss_pct=rules.max_daily_loss_pct,
        allowed_autonomy=DomainAutonomy(rules.allowed_autonomy.value),
        forbidden_assets=tuple(rules.forbidden_assets),
    )
    proposed = replace(proposed, revision_hash=compute_revision_hash(proposed))

    if detect_material_change(current_active, proposed):
        proposed = replace(proposed, cooling_off_started_at=datetime.now(timezone.utc))

    created = await repo.insert_draft_revision(
        mandate_id=mandate.id, revision_no=next_revision_no, rules=proposed
    )

    if audit_repo is not None and proposer_id is not None:
        await record_command_event(
            audit_repo,
            tenant_id=tenant_id,
            aggregate_type="mandate_revision",
            aggregate_id=created.id,
            action=PROPOSED_ACTION,
            actor_subject_id=proposer_id,
            payload={"mandate_id": str(mandate.id), "revision_no": next_revision_no},
        )

    return revision_to_view(created)
