"""UpsertRiskLimit 커맨드 — R-26.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4/§9 R-26.

78번 §2.6 "한도 생성·변경은 운영자·risk officer만" — 일반 tenant 사용자는
호출할 수 없다(`UnauthorizedLimitActorError`). risk officer는 자기 tenant
범위 밖(다른 tenant, 플랫폼 기본값 `tenant_id=None`)의 한도는 건드릴 수
없다(`CrossTenantLimitScopeError`) — 그건 운영자만 가능하다.

`audit_repo`는 activate_safety_control.py처럼 `None` 기본값을 두지 않는다
— 한도 변경은 반드시 감사 1행을 남겨야 하는 커맨드라(DoD) 호출자가 실수로
감사 없이 호출하는 경로 자체를 타입으로 막는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification as AuditClassification
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.risk_gate.domain.models import RiskLimit
from src.foundation.risk_gate.ports.repository import RiskLimitRepository


@dataclass(frozen=True)
class LimitActor:
    subject_id: UUID
    is_operator: bool = False
    is_risk_officer: bool = False


class UnauthorizedLimitActorError(Exception):
    """운영자도 risk officer도 아닌 행위자가 한도 upsert를 시도했다."""


class CrossTenantLimitScopeError(Exception):
    """risk officer가 자기 tenant 범위(플랫폼 기본값 포함) 밖의 한도를
    건드리려 했다 — 운영자 권한이 필요하다."""


async def upsert_risk_limit(
    repo: RiskLimitRepository,
    audit_repo: AuditEventRepository,
    *,
    tenant_id: UUID,
    actor: LimitActor,
    limit: RiskLimit,
) -> RiskLimit:
    if not (actor.is_operator or actor.is_risk_officer):
        raise UnauthorizedLimitActorError(
            f"{actor.subject_id}: 운영자 또는 risk officer만 한도를 생성·변경할 수 있습니다."
        )
    if not actor.is_operator and limit.tenant_id != tenant_id:
        raise CrossTenantLimitScopeError(
            "risk officer는 자기 tenant 범위(플랫폼 기본값 포함)를 벗어난 한도를 "
            "다룰 수 없습니다 — 운영자 권한이 필요합니다."
        )

    saved = await repo.upsert(limit)

    await record_command_event(
        audit_repo,
        tenant_id=saved.tenant_id,
        aggregate_type="risk_limit",
        aggregate_id=saved.id,
        action="risk_limit_upserted",
        actor_subject_id=actor.subject_id,
        classification=AuditClassification.CONFIDENTIAL,
        payload={
            "scope": saved.scope.value,
            "scope_ref": saved.scope_ref,
            "metric": saved.metric.value,
            "limit_value": str(saved.limit_value),
            "hard": saved.hard,
        },
    )

    return saved


__all__ = [
    "CrossTenantLimitScopeError",
    "LimitActor",
    "UnauthorizedLimitActorError",
    "upsert_risk_limit",
]
