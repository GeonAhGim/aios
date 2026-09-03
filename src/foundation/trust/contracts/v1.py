"""Trust Core 계약 v1.

Spec: AIOSproject 43_trust_core_identity_consent_suitability_specification_v1.0.md,
73_trust_core_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.

이 파일의 클래스가 "계약"이다 — 다른 bounded context(FND-02 이후)는 이 파일을
import해서 소비하고, 이 파일이 아닌 domain/models.py를 직접 참조하지 않는다
(106번 §5, 71번 §4 Contract ownership 규칙).

MAJOR 변경 시 이 파일을 고치지 않고 `contracts/v2.py`를 새로 만든다(107번 §3.3).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class ConsentState(str, Enum):
    NONE = "NONE"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class TenantContext(BaseModel):
    """71번 §4 — API body에서 생성 금지. 게이트웨이 인증(`get_current_user`)에서만
    발급한다.

    P0 스콥(개인 계정)에서는 tenant_id == subject_id다 — 73번 §2 "Tenant:
    isolation boundary for personal account, household, or organization" 중
    household/organization(복수 구성원)은 66번 문서의 후속 범위이며 이 계약은
    아직 그 확장을 다루지 않는다. 즉 지금은 `Membership` 상태 머신(grant/
    suspend/revoke)을 별도로 구현하지 않는다 — tenant마다 구성원이 정확히
    하나뿐인 상태에서 "초대/추방"할 대상이 없기 때문이다(35번 §9.2 "미세한
    파일 분할만 늘리고 contract·owner·test가 없는 모듈 증식도 금지" 원칙에
    따라, 쓰이지 않을 상태 전이를 미리 만들지 않는다).
    """

    tenant_id: UUID
    subject_id: UUID
    role: str = "OWNER"
    mfa_verified: bool
    schema_version: str = SCHEMA_VERSION
    # PLT-28(107번 §3.2 MINOR) — household/organization tenant는 membership
    # 행으로 role을 얻으므로, 그 행을 역참조할 수 있게 optional로 붙인다.
    # personal tenant(membership 상태 머신 없음, 위 참조)는 계속 None.
    # 기존 필드는 그대로이므로 이 계약을 소비하던 v1 fixture는 무수정 통과한다.
    membership_id: UUID | None = None


class ConsentDecision(BaseModel):
    """AcceptDisclosure/RevokeConsent 커맨드의 결과이자, 다른 context가 소비하는
    계약. 원문 disclosure 본문이나 사용자 답변은 담지 않는다(73번 §8)."""

    consent_id: UUID
    tenant_id: UUID
    purpose: str
    disclosure_id: UUID
    disclosure_revision: int
    state: ConsentState
    accepted_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None
    schema_version: str = SCHEMA_VERSION


class TrustFreshnessDecision(BaseModel):
    """EvaluateTrustFreshness 쿼리의 결과 — Mandate/Package/Paper Control 등
    소비자가 "이 tenant가 이 purpose에 대해 유효한 동의를 갖고 있는가"만
    확인할 때 쓴다(73번 §4 EvaluateTrustFreshness)."""

    tenant_id: UUID
    purpose: str
    is_fresh: bool
    reason_code: str | None  # 72번 §4 에러 taxonomy, 예: "POLICY_CONSENT_REVOKED"
    as_of: datetime
    schema_version: str = SCHEMA_VERSION
