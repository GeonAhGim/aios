"""Risk & Safety Gate repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.foundation.risk_gate.domain.models import (
    FenceSnapshot,
    RiskEvaluation,
    RiskLimit,
    SafetyControl,
    SafetyScope,
)


class RiskGateRepository(Protocol):
    async def list_active_controls(
        self,
        *,
        tenant_id: UUID,
        provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        """78번 §2 "active controls compose by most restrictive outcome" —
        GLOBAL + 이 tenant + (지정 시) 이 provider에 해당하는 ACTIVE 행만
        반환한다. 다른 tenant/provider의 control은 반환하지 않는다.

        `include_all_providers=True`면 특정 provider_code로 좁히지 않고
        PROVIDER 범위 전체를 반환한다 — 레드팀 #2026-09-02-27 반영. 평가
        시점(evaluate_risk_gate)에는 특정 connection의 provider_code만
        알면 되지만, 운영자 Control Center 목록(projections.py)은 "지금
        걸려있는 모든 통제"를 보여줘야 하므로 특정 provider로 좁히면 안
        된다."""
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

    async def read_fences(
        self, pairs: tuple[tuple[SafetyScope, str], ...]
    ) -> FenceSnapshot:
        """78번 §3.6 — 지정된 (scope, scope_ref) 쌍들의 현재 fence 토큰을
        `WHERE (scope,scope_ref) IN (...)` 단일 쿼리로 조회한다(쌍마다 왕복
        하지 않는다). 아직 한 번도 activate되지 않아 행이 없는 쌍은 토큰
        0으로 채워 반환한다(구현체 책임 — `FenceSnapshot.tokens`는 요청한
        모든 pairs를 키로 갖는다)."""
        ...

    async def invalidate_evaluations(self, *, tenant_id: UUID | None) -> None:
        """레드팀 #2026-09-02-26 반영 — safety control이 새로 걸리거나
        해제됐을 때, 그 변화가 캐시 TTL(10초)이 자연 만료될 때까지
        기다리지 않고 즉시 반영되도록 캐시를 지운다. `tenant_id=None`이면
        전체 tenant(GLOBAL/PROVIDER 범위처럼 시스템 전역에 영향을 주는
        control)의 캐시를 지운다."""
        ...


class RiskLimitRepository(Protocol):
    """R-26 — `risk_limit`/`risk_limit_breach` 저장소 포트."""

    async def list_effective(
        self,
        tenant_id: UUID,
        *,
        provider_code: str | None = None,
        strategy_id: str | None = None,
        symbols: tuple[str, ...] | None = None,
    ) -> tuple[RiskLimit, ...]:
        """이 tenant 소유 행 또는 플랫폼 기본값(`tenant_id IS NULL`)만
        반환한다 — 다른 tenant의 한도는 절대 섞이지 않는다(구현체 책임)."""
        ...

    async def upsert(self, limit: RiskLimit) -> RiskLimit:
        """§6 표 낙관적 잠금 upsert. `limit.updated_at`은 호출자가 마지막으로
        읽은 기대값(신규 생성이면 `None`) — 실제와 다르면
        `ConcurrencyConflictError`(구현체 책임, 무조건 성공 위장 금지)."""
        ...

    async def record_breach(
        self,
        *,
        limit_id: UUID,
        decision_id: UUID,
        observed: Decimal,
        limit_value: Decimal,
        severity: str,
        occurred_at: datetime,
    ) -> int: ...


class RuleBundleRepository(Protocol):
    """R-22/R-23 — `risk_rule_bundle` 저장소 포트(`adapters/postgres_bundle_repository.py`)."""

    async def get_active(self, scope: str) -> RiskRuleBundle | None: ...

    async def get_by_id(self, bundle_id: UUID) -> RiskRuleBundle | None: ...

    async def insert_draft(self, bundle: RiskRuleBundle) -> RiskRuleBundle: ...

    async def transition(
        self,
        bundle_id: UUID,
        *,
        expected_state: BundleState,
        new_state: BundleState,
        **audit: Any,
    ) -> RiskRuleBundle: ...
