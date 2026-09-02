"""CreateMandate 커맨드 — 첫 DRAFT revision을 만든다.

Spec: AIOSproject 45번 §3 (`CreateMandate` -> draft revision).
"""
from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from src.foundation.mandates.contracts.v1 import Autonomy as ContractAutonomy
from src.foundation.mandates.contracts.v1 import MandateRevisionState as ContractRevisionState
from src.foundation.mandates.contracts.v1 import MandateRevisionView, MandateRuleInput
from src.foundation.mandates.domain.models import Autonomy as DomainAutonomy
from src.foundation.mandates.domain.models import MandateRevision as DomainRevision
from src.foundation.mandates.domain.models import MandateRevisionState as DomainRevisionState
from src.foundation.mandates.domain.rules import compute_revision_hash
from src.foundation.mandates.ports.repository import MandateRepository


def revision_to_view(revision: DomainRevision) -> MandateRevisionView:
    """domain -> contract 매핑. 71번 §4 "domain은 다른 계층에 의존하지 않는다"의
    반대 방향(application이 domain을 contract로 번역)이라, contract enum과
    domain enum이 같은 문자열 값이어도 명시적으로 변환한다(암묵적 str 강제
    변환에 기대지 않는다 — 두 enum이 나중에 갈라져도 여기서 컴파일 타임에
    걸린다)."""
    return MandateRevisionView(
        id=revision.id,
        mandate_id=revision.mandate_id,
        revision_no=revision.revision_no,
        state=ContractRevisionState(revision.state.value),
        max_total_exposure_pct=revision.max_total_exposure_pct,
        max_single_instrument_pct=revision.max_single_instrument_pct,
        min_cash_buffer_pct=revision.min_cash_buffer_pct,
        max_daily_loss_pct=revision.max_daily_loss_pct,
        allowed_autonomy=ContractAutonomy(revision.allowed_autonomy.value),
        forbidden_assets=list(revision.forbidden_assets),
        revision_hash=revision.revision_hash,
        cooling_off_started_at=revision.cooling_off_started_at,
        created_at=revision.created_at,
        activated_at=revision.activated_at,
    )


class MandateAlreadyExistsError(Exception):
    """이 리프 스콥(tenant당 mandate 1개)에서, 이미 DRAFT나 PROPOSED가 있는데
    또 새 draft를 만들려는 시도. ACTIVE 위에 새 초안을 얹으려면
    propose_amendment를 쓴다."""


async def create_draft_mandate(
    repo: MandateRepository,
    *,
    tenant_id: UUID,
    subject_id: UUID,
    rules: MandateRuleInput,
) -> MandateRevisionView:
    mandate = await repo.get_or_create_mandate(tenant_id, subject_id)
    existing_revisions = await repo.list_revisions(mandate.id)
    if any(
        r.state in (DomainRevisionState.DRAFT, DomainRevisionState.PROPOSED)
        for r in existing_revisions
    ):
        raise MandateAlreadyExistsError(
            "이미 진행 중인 DRAFT/PROPOSED revision이 있습니다 — 그것을 먼저 처리하세요."
        )

    next_revision_no = len(existing_revisions) + 1
    draft = DomainRevision(
        id=uuid4(),
        mandate_id=mandate.id,
        revision_no=next_revision_no,
        state=DomainRevisionState.DRAFT,
        max_total_exposure_pct=rules.max_total_exposure_pct,
        max_single_instrument_pct=rules.max_single_instrument_pct,
        min_cash_buffer_pct=rules.min_cash_buffer_pct,
        max_daily_loss_pct=rules.max_daily_loss_pct,
        allowed_autonomy=DomainAutonomy(rules.allowed_autonomy.value),
        forbidden_assets=tuple(rules.forbidden_assets),
    )
    draft = replace(draft, revision_hash=compute_revision_hash(draft))

    created = await repo.insert_draft_revision(
        mandate_id=mandate.id, revision_no=next_revision_no, rules=draft
    )
    return revision_to_view(created)
