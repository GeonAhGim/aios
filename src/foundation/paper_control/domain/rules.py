"""Paper Execution & Control 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
)

# 77번 §2 상태 전이표. REQUESTED->READY는 이 리프에서 REQUEST+PREPARE를
# 하나의 커맨드(request_deployment)로 합쳐 즉시 수행한다 — 두 커맨드
# 사이에 실제 비동기 대기(예: 외부 provider 승인)가 없어(connections의
# ConfirmConnection과 다른 이유로) 나눌 이유가 없다. PREPARING이라는 중간
# 상태 자체는 만들지 않는다(명시적 스콥 축소, 마이그레이션 docstring 참조).
_ALLOWED_TRANSITIONS: dict[DeploymentState, frozenset[DeploymentState]] = {
    DeploymentState.REQUESTED: frozenset({DeploymentState.READY, DeploymentState.FAILED}),
    DeploymentState.READY: frozenset({DeploymentState.RUNNING, DeploymentState.STOPPED}),
    DeploymentState.RUNNING: frozenset(
        {DeploymentState.PAUSED, DeploymentState.STOPPED, DeploymentState.DEGRADED}
    ),
    DeploymentState.PAUSED: frozenset(
        {DeploymentState.RUNNING, DeploymentState.STOPPED, DeploymentState.DEGRADED}
    ),
    DeploymentState.DEGRADED: frozenset(
        {DeploymentState.STOPPED, DeploymentState.RECOVERY_REVIEW}
    ),
    DeploymentState.RECOVERY_REVIEW: frozenset({DeploymentState.STOPPED}),
    DeploymentState.STOPPED: frozenset(),
    DeploymentState.FAILED: frozenset(),
}
"""RECOVERY_REVIEW -> RUNNING이 표에 없는 것 자체가 77번 §2 "RECOVERY_REVIEW
cannot transition to RUNNING automatically"의 구현이다 — resume_deployment()는
PAUSED에서만 호출 가능하고, RECOVERY_REVIEW를 벗어나는 유일한 길은 STOP뿐
(사람이 새 REQUEST로 완전히 새 deployment를 만들게 강제)."""


class InvalidDeploymentTransitionError(Exception):
    pass


class InvalidProvenanceError(Exception):
    """77번 §1 "A boolean is_paper alone is insufficient" / PAP-002 —
    credential_class가 PAPER가 아니거나 필수 필드가 비어있으면 adapter를
    부르기도 전에 거부한다."""


def is_transition_allowed(current: DeploymentState, target: DeploymentState) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def require_transition_allowed(current: DeploymentState, target: DeploymentState) -> None:
    if not is_transition_allowed(current, target):
        raise InvalidDeploymentTransitionError(
            f"{current.value} -> {target.value} 전이는 허용되지 않습니다."
        )


def validate_provenance(provenance: AdapterProvenance) -> None:
    """PAP-002 "live endpoint, live credential, unknown adapter, or missing
    egress proof rejects before adapter call" — 이 리프에서 "egress proof"는
    `provider_sandbox_account_ref`로 대체한다(실 egress policy 서비스가 아직
    없음, 71번 §4 Contract ownership 경계 밖)."""
    if provenance.credential_class != CredentialClass.PAPER:
        raise InvalidProvenanceError(
            f"credential_class={provenance.credential_class.value}는 PAPER가 아닙니다."
        )
    if not provenance.adapter_type.strip():
        raise InvalidProvenanceError("adapter_type이 비어있습니다.")
    if not provenance.provider_sandbox_account_ref.strip():
        raise InvalidProvenanceError("provider_sandbox_account_ref가 비어있습니다.")
    if "live" in provenance.endpoint_classification.lower():
        raise InvalidProvenanceError(
            f"endpoint_classification={provenance.endpoint_classification}는 "
            "LIVE 엔드포인트로 의심됩니다."
        )
