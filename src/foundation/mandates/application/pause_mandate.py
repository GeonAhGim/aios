"""PauseMandate / ResumeMandate 커맨드.

Spec: AIOSproject 45번 §3 (`PauseMandate` -> mandate pause event), 75번 §2
"ACTIVE -> PAUSED; PAUSED -> ACTIVE needs fresh policy evaluation."

resume 시 "fresh policy evaluation"을 강제하는 방법(75번 §2)은 이 리프에서
서버가 재평가를 대신 실행해주는 게 아니라, resume 직후 상태를 ACTIVE로
되돌리기만 하고 실제 재평가는 evaluate_policy()가 다음 호출에서 항상
최신 active revision을 기준으로 하므로 자동으로 만족된다(캐시된 이전
PolicyDecision을 재사용하지 않는다 — get_cached_decision()은 fingerprint가
같을 때만 히트한다).
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.mandates.application.create_draft_mandate import revision_to_view
from src.foundation.mandates.contracts.v1 import MandateRevisionView
from src.foundation.mandates.domain.models import MandateRevisionState
from src.foundation.mandates.ports.repository import MandateRepository


class NoActiveMandateError(Exception):
    pass


async def pause_mandate(repo: MandateRepository, *, tenant_id: UUID) -> MandateRevisionView:
    mandate = await repo.get_mandate(tenant_id)
    if mandate is None:
        raise NoActiveMandateError(str(tenant_id))
    active = await repo.get_active_revision(mandate.id)
    if active is None:
        raise NoActiveMandateError(str(tenant_id))

    paused = await repo.transition_revision_state(
        active.id,
        expected_state=MandateRevisionState.ACTIVE.value,
        new_state=MandateRevisionState.PAUSED.value,
    )
    return revision_to_view(paused)


async def resume_mandate(repo: MandateRepository, *, tenant_id: UUID) -> MandateRevisionView:
    mandate = await repo.get_mandate(tenant_id)
    if mandate is None:
        raise NoActiveMandateError(str(tenant_id))
    active_revision_id = mandate.active_revision_id
    if active_revision_id is None:
        raise NoActiveMandateError(str(tenant_id))

    resumed = await repo.transition_revision_state(
        active_revision_id,
        expected_state=MandateRevisionState.PAUSED.value,
        new_state=MandateRevisionState.ACTIVE.value,
    )
    return revision_to_view(resumed)
