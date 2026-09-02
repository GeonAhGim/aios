"""ActivateMandate + ApproveMaterialChange를 하나로 묶은 커맨드.

Spec: AIOSproject 45번 §3, 75번 §2.

스콥 축소(명시): 45번은 이 둘을 별도 커맨드로 나누지만, 이 리프는 하나로
합친다 — "이 revision을 활성화해도 되는가"라는 같은 질문에 대해, 이전
ACTIVE revision이 없으면(최초 활성화) 게이트가 필요 없고, 있으면(개정)
material change 여부에 따라 게이트가 켜진다는 하나의 판단 트리로
표현하는 게 두 커맨드로 쪼개는 것보다 명확하다.

material change 게이트(75번 §2)는 원래 별도 승인자(approval_binding)를
요구하지만, FND-01 마이그레이션과 같은 이유로 이 코드베이스엔 그 대상이
없다 — 대신 기존 `reauthenticate()`(비밀번호+MFA)와 FND-01 Trust Core의
동의 신선도, 그리고 cooling-off 경과 시간 3중 게이트로 대체한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.mandates.application.create_draft_mandate import revision_to_view
from src.foundation.mandates.contracts.v1 import MandateRevisionView
from src.foundation.mandates.domain.models import MandateRevisionState
from src.foundation.mandates.domain.rules import detect_material_change
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from src.foundation.trust.ports.repository import TrustRepository

MATERIAL_CHANGE_CONSENT_PURPOSE = "portfolio_mandate_material_change"
MIN_COOLING_OFF_SECONDS = 60  # approval_settings_service.py의 기본 mandatory_wait_seconds와 동일


class RevisionNotFoundError(Exception):
    pass


class CrossTenantMandateAccessError(Exception):
    """73번 TRU-006과 동일 원칙 — 다른 tenant의 revision은 존재 여부도 흘리지
    않고 거부한다(호출부가 404로 통일해 매핑)."""


class InvalidRevisionStateError(Exception):
    """DRAFT/PROPOSED가 아닌 revision(이미 ACTIVE/SUPERSEDED/CANCELLED)은
    activate 대상이 아니다."""


class MaterialChangeRequiresReauthError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(f"material change({reasons})는 재인증이 필요합니다.")
        self.reasons = reasons


class MaterialChangeRequiresFreshConsentError(Exception):
    def __init__(self, reason_code: str | None) -> None:
        super().__init__(f"material change에는 최신 동의가 필요합니다: {reason_code}")
        self.reason_code = reason_code


class CoolingOffNotElapsedError(Exception):
    def __init__(self, remaining_seconds: float) -> None:
        super().__init__(f"cooling-off이 아직 {remaining_seconds:.0f}초 남았습니다.")
        self.remaining_seconds = remaining_seconds


async def activate_revision(
    mandate_repo: MandateRepository,
    trust_repo: TrustRepository,
    *,
    tenant_id: UUID,
    subject_id: UUID,
    revision_id: UUID,
    reauthenticated: bool,
) -> MandateRevisionView:
    revision = await mandate_repo.get_revision(revision_id)
    if revision is None:
        raise RevisionNotFoundError(str(revision_id))

    mandate = await mandate_repo.get_mandate(tenant_id)
    if mandate is None or revision.mandate_id != mandate.id:
        raise CrossTenantMandateAccessError(str(revision_id))

    if revision.state not in (MandateRevisionState.DRAFT, MandateRevisionState.PROPOSED):
        raise InvalidRevisionStateError(f"{revision.state.value}는 activate할 수 없습니다.")

    current_active = await mandate_repo.get_active_revision(mandate.id)
    if current_active is not None:
        material_reasons = detect_material_change(current_active, revision)
        if material_reasons:
            if not reauthenticated:
                raise MaterialChangeRequiresReauthError(material_reasons)

            trust_context = TrustTenantContext(
                tenant_id=tenant_id, subject_id=subject_id, mfa_verified=reauthenticated
            )
            freshness = await evaluate_trust_freshness(
                trust_repo, trust_context, purpose=MATERIAL_CHANGE_CONSENT_PURPOSE
            )
            if not freshness.is_fresh:
                raise MaterialChangeRequiresFreshConsentError(freshness.reason_code)

            if revision.cooling_off_started_at is None:
                raise CoolingOffNotElapsedError(remaining_seconds=MIN_COOLING_OFF_SECONDS)
            elapsed = (
                datetime.now(timezone.utc) - revision.cooling_off_started_at
            ).total_seconds()
            if elapsed < MIN_COOLING_OFF_SECONDS:
                raise CoolingOffNotElapsedError(
                    remaining_seconds=MIN_COOLING_OFF_SECONDS - elapsed
                )

    activated = await mandate_repo.activate_revision(mandate.id, revision_id)
    return revision_to_view(activated)
