"""ApproveRuleBundle/ActivateRuleBundle 커맨드 — R-23.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4/§9 R-23.

상태기계 DRAFT→APPROVED→ACTIVE는 R-15(`src.core.risk.policy_bundle`)가 순수
판정만 정의하고, 실제 원자적 전이는 R-22 `postgres_bundle_repository.transition`
의 `conditional_update`가 한다(§2.6 a9c4e1f7b2d3) — 이 모듈은 새 전이 규칙을
만들지 않고, 각 커맨드가 요구하는 고정된 `expected_state`/`new_state` 쌍으로
그 헬퍼를 호출할 뿐이다. scope당 ACTIVE 1개(I6)는 partial unique
`ux_bundle_active`가 커밋 시점에 강제하므로 여기서 다시 검사하지 않는다.

4-eyes 원칙(승인자 ≠ 작성자)을 검사하려면 승인 대상 DRAFT 번들의 `created_by`
를 먼저 읽어야 하는데, R-22가 만든 3개 메서드(`get_active`/`insert_draft`/
`transition`) 중 `get_active`는 ACTIVE 상태만 보여 DRAFT/APPROVED 번들을 찾지
못한다 — `postgres_bundle_repository.get_by_id`(이 리프에서 추가, 신규
상태전이 로직이 아니라 단순 조회)로 그 간극만 메운다.

미검증: `actor_is_risk_officer`는 호출자(라우터)가 `User.is_platform_admin`으로
근사해 넘긴다 — 이 코드베이스에는 아직 전용 risk officer 역할 필드가 없다
(78번 §2.6 risk officer는 policy 기획서 상의 역할일 뿐 인증 계층에 없음).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification as AuditClassification
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.risk_gate.ports.repository import RuleBundleRepository


class UnauthorizedRuleBundleActorError(Exception):
    """risk officer가 아닌 행위자가 rule bundle 승인/활성화를 시도했다."""


class RuleBundleNotFoundError(Exception):
    pass


class SelfApprovalError(Exception):
    """승인자가 작성자(`created_by`)와 동일 — 4-eyes 승인 원칙 위반."""


class MissingApprovalRefError(Exception):
    """approval_ref 없이는 ADR/증적 없는 승인이 된다 — fail-closed 거부."""


async def approve_rule_bundle(
    repo: RuleBundleRepository,
    audit_repo: AuditEventRepository,
    *,
    bundle_id: UUID,
    approver_subject_id: UUID,
    approval_ref: str,
    actor_is_risk_officer: bool,
) -> RiskRuleBundle:
    if not actor_is_risk_officer:
        raise UnauthorizedRuleBundleActorError(
            f"{approver_subject_id}: risk officer만 rule bundle을 승인할 수 있습니다."
        )
    if not approval_ref:
        raise MissingApprovalRefError("approval_ref는 필수입니다 — ADR 등 승인 근거를 남기세요.")

    bundle = await repo.get_by_id(bundle_id)
    if bundle is None:
        raise RuleBundleNotFoundError(str(bundle_id))
    if bundle.created_by == approver_subject_id:
        raise SelfApprovalError(
            "작성자 본인은 자신이 만든 rule bundle을 승인할 수 없습니다(4-eyes)."
        )

    approved = await repo.transition(
        bundle_id,
        expected_state=BundleState.DRAFT,
        new_state=BundleState.APPROVED,
        approved_by=approver_subject_id,
        approval_ref=approval_ref,
        approved_at=datetime.now(timezone.utc),
    )

    await record_command_event(
        audit_repo,
        tenant_id=None,
        aggregate_type="risk_rule_bundle",
        aggregate_id=approved.id,
        action="risk_rule_bundle_approved",
        actor_subject_id=approver_subject_id,
        classification=AuditClassification.CONFIDENTIAL,
        payload={
            "scope": approved.scope,
            "version": approved.version,
            "approval_ref": approval_ref,
        },
    )
    return approved


async def activate_rule_bundle(
    repo: RuleBundleRepository,
    audit_repo: AuditEventRepository,
    *,
    bundle_id: UUID,
    actor_subject_id: UUID,
    actor_is_risk_officer: bool,
) -> RiskRuleBundle:
    if not actor_is_risk_officer:
        raise UnauthorizedRuleBundleActorError(
            f"{actor_subject_id}: risk officer만 rule bundle을 활성화할 수 있습니다."
        )

    now = datetime.now(timezone.utc)
    activated = await repo.transition(
        bundle_id,
        expected_state=BundleState.APPROVED,
        new_state=BundleState.ACTIVE,
        activated_at=now,
        effective_from=now,
    )

    await record_command_event(
        audit_repo,
        tenant_id=None,
        aggregate_type="risk_rule_bundle",
        aggregate_id=activated.id,
        action="risk_rule_bundle_activated",
        actor_subject_id=actor_subject_id,
        classification=AuditClassification.CONFIDENTIAL,
        payload={"scope": activated.scope, "version": activated.version},
    )
    return activated


__all__ = [
    "MissingApprovalRefError",
    "RuleBundleNotFoundError",
    "SelfApprovalError",
    "UnauthorizedRuleBundleActorError",
    "activate_rule_bundle",
    "approve_rule_bundle",
]
