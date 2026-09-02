"""Risk & Safety Gate repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.risk_gate.domain.models import RiskEvaluation, SafetyControl, SafetyScope


class RiskGateRepository(Protocol):
    async def list_active_controls(
        self, *, tenant_id: UUID, provider_code: str | None = None
    ) -> tuple[SafetyControl, ...]:
        """78번 §2 "active controls compose by most restrictive outcome" —
        GLOBAL + 이 tenant + (지정 시) 이 provider에 해당하는 ACTIVE 행만
        반환한다. 다른 tenant/provider의 control은 반환하지 않는다."""
        ...

    async def insert_safety_control(
        self,
        *,
        scope: SafetyScope,
        scope_ref: str,
        reason: str,
        actor_subject_id: UUID,
    ) -> SafetyControl:
        """fence_token을 원자적으로 증가시키며 새 control 행을 만든다(구현체
        책임 — 105번 표준과 달리 이건 낙관적 동시성이 아니라 단조증가
        카운터라 conditional_update가 아닌 별도 fence 테이블의 단일 UPDATE로
        처리한다, adapters/postgres_repository.py 참조)."""
        ...

    async def get_safety_control(self, control_id: UUID) -> SafetyControl | None: ...

    async def deactivate_safety_control(self, control_id: UUID) -> SafetyControl:
        """48번 §4 "해제는 ... 독립 recovery decision 없이는 불가" — 이
        메서드 자체는 그 recovery decision이 아니라, INACTIVE로 표시해
        "복구 검토가 가능한 상태"로만 만든다(78번 §3 DeactivateSafetyControl
        "cannot create RUNNING state" — 이 코드베이스엔 아직 RUNNING을 만들
        paper_control 자체가 없어 자연히 지켜진다)."""
        ...

    async def insert_evaluation(self, evaluation: RiskEvaluation) -> RiskEvaluation: ...

    async def get_cached_evaluation(
        self, tenant_id: UUID, fingerprint: str
    ) -> RiskEvaluation | None:
        """75번 mandates의 get_cached_decision과 동일 원칙 — 짧은 TTL 캐시."""
        ...
