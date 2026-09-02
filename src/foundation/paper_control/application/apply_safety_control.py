"""ApplySafetyControlToDeployments — kill switch가 활성화되면 이미 RUNNING인
배포를 실제로 PAUSED로 전이시킨다(fence 소비).

Spec: AIOSproject 77번 §2 "STOP and risk/emergency PAUSE outrank
START/RESUME" — 이 "risk/emergency PAUSE"가 이 함수다. submit_paper_intent()의
PRE_INTENT 게이트(교차세션 감사 발견, agent-platform-12)는 다음 제출을 막을
뿐 deployment.state를 바꾸지 않는다 — 대시보드/운영자가 보는 상태가
"RUNNING(하지만 막힘)"으로 남아있으면 오해를 부른다. kill switch 활성화
직후 영향받는 배포를 실제로 PAUSED로 옮겨 상태 자체가 진실을 반영하게 한다.

48번 §4 kill switch 범위(GLOBAL/TENANT/ACCOUNT/PROVIDER/STRATEGY_DEPLOYMENT)
중 이 함수가 실제로 처리하는 건 GLOBAL/TENANT/ACCOUNT뿐이다:
- TENANT/ACCOUNT: scope_ref가 곧 tenant_id — 그 tenant의 RUNNING 배포만.
- GLOBAL: 모든 tenant의 RUNNING 배포(48번 §4 "global kill switch는 모든
  tenant의 새 주문을 막되 각 tenant 증적을 보존한다").
- PROVIDER: paper_control은 connection_id만 알고 provider_code 자체는
  connections(FND-05) 컨텍스트 소유다(71번 §4 경계) — 여기서 새로 매핑을
  만들지 않는다(미사용 인프라를 미리 만들지 않는다는 이 세션 원칙과 동일).
- STRATEGY_DEPLOYMENT: risk_gate 자신의 activate_safety_control.py가 이미
  "생성해도 아직 어떤 평가에도 영향을 주지 못한다"고 명시한 의도된
  선반영 범위다 — 같은 미완성 상태를 두 곳에서 다르게 다루면 더 혼란스럽다.

의존 방향: 이 함수는 risk_gate의 도메인 enum(SafetyScope)만 값으로 받을 뿐
risk_gate의 repository/port는 전혀 모른다 — paper_control -> risk_gate
단방향 의존(submit_paper_intent.py가 이미 risk_gate.domain을 참조하는
것과 같은 방향, 반대 방향은 없다).
"""
from __future__ import annotations

from uuid import UUID

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.paper_control.application.request_deployment import deployment_to_view
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.models import (
    CommandOutcome,
    CommandType,
    DeploymentState,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.domain.models import SafetyScope

_TENANT_SCOPED = frozenset({SafetyScope.TENANT, SafetyScope.ACCOUNT})


async def apply_safety_control_to_deployments(
    repo: PaperControlRepository,
    *,
    scope: SafetyScope,
    scope_ref: str,
    safety_control_id: UUID,
    actor_subject_id: UUID,
    reason: str,
) -> list[PaperDeploymentView]:
    if scope in _TENANT_SCOPED:
        candidates = await repo.list_deployments(UUID(scope_ref))
    elif scope == SafetyScope.GLOBAL:
        candidates = await repo.list_running_deployments()
    else:
        return []

    idempotency_key = f"risk-pause:{safety_control_id}"
    paused: list[PaperDeploymentView] = []
    for deployment in candidates:
        if deployment.state != DeploymentState.RUNNING:
            continue
        try:
            updated = await repo.increment_fence(
                deployment.id,
                expected_state=DeploymentState.RUNNING.value,
                new_state=DeploymentState.PAUSED.value,
            )
        except ConcurrencyConflictError:
            # 77번 §2 "STOP outranks PAUSE"와 같은 우선순위 원칙 — 그 사이
            # 다른 커맨드(STOP 등)가 먼저 상태를 바꿨다면 이 전이는 조용히
            # 진다. 전체 fan-out을 실패시킬 이유가 아니다(개별 배포 하나가
            # 이미 안전한 상태로 갔다는 뜻).
            continue
        await repo.insert_command(
            deployment_id=deployment.id,
            idempotency_key=idempotency_key,
            command_type=CommandType.PAUSE,
            actor_subject_id=actor_subject_id,
            outcome=CommandOutcome.ACCEPTED,
            detail=f"RISK_GATE_SAFETY_CONTROL:{safety_control_id}:{reason}",
        )
        paused.append(deployment_to_view(updated))
    return paused
