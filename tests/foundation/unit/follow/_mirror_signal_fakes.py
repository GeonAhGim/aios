"""`test_mirror_signal.py`용 인메모리 테스트 더블 — 테스트 케이스 본문과
분리한다(file policy ADR-2026-09-10-C §7, `tests/foundation/unit/ai/factory/
_promote_to_paper_fakes.py`와 동일한 분리 관례).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyBundle,
    PolicyDecision,
    PortfolioMandate,
)
from src.foundation.risk_gate.domain.models import RiskEvaluation, SafetyControl

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


class FakeRiskGateRepository:
    def __init__(self, *, active_controls: tuple[SafetyControl, ...] = ()) -> None:
        self.active_controls = active_controls
        self.list_active_controls_calls = 0
        self.insert_evaluation_calls = 0
        self.fail_insert_evaluation: BaseException | None = None

    async def get_cached_evaluation(
        self, tenant_id: UUID, fingerprint: str
    ) -> RiskEvaluation | None:
        return None

    async def list_active_controls(
        self,
        *,
        tenant_id: UUID,
        provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        self.list_active_controls_calls += 1
        return self.active_controls

    async def insert_evaluation(self, evaluation: RiskEvaluation) -> RiskEvaluation:
        self.insert_evaluation_calls += 1
        if self.fail_insert_evaluation is not None:
            raise self.fail_insert_evaluation
        return evaluation


class FakeMandateRepository:
    """리스크 게이트가 부르는 `evaluate_policy`와 컴플라이언스 게이트가 부르는
    `evaluate_pre_trade`가 공유하는 하나의 ACTIVE revision — `forbidden_assets`를
    채우면 실제 `restricted_list` 규칙이 진짜 DENY를 낸다(스텁이 아니라 실제
    규칙 코드 경로, "게이트 적색 재현" D2 요건)."""

    def __init__(self, *, forbidden_assets: tuple[str, ...] = ()) -> None:
        self.mandate_id = uuid4()
        self.revision_id = uuid4()
        self._revision = MandateRevision(
            id=self.revision_id,
            mandate_id=self.mandate_id,
            revision_no=1,
            state=MandateRevisionState.ACTIVE,
            max_total_exposure_pct=100.0,
            max_single_instrument_pct=100.0,
            min_cash_buffer_pct=0.0,
            max_daily_loss_pct=100.0,
            allowed_autonomy=Autonomy.PAPER,
            forbidden_assets=forbidden_assets,
        )
        self._bundle: PolicyBundle | None = None
        self._decisions: dict[tuple[UUID, str], PolicyDecision] = {}
        self.get_mandate_calls = 0
        self.policy_decision_command_types: list[str] = []

    async def get_mandate(
        self, tenant_id: UUID, portfolio_id: UUID | None = None
    ) -> PortfolioMandate:
        self.get_mandate_calls += 1
        return PortfolioMandate(
            id=self.mandate_id,
            tenant_id=tenant_id,
            subject_id=uuid4(),
            portfolio_id=portfolio_id if portfolio_id is not None else uuid4(),
            active_revision_id=self.revision_id,
            created_at=NOW,
        )

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        return self._revision if revision_id == self.revision_id else None

    async def get_bundle_for_revision(self, revision_id: UUID) -> PolicyBundle | None:
        return self._bundle

    async def insert_policy_bundle(self, bundle: PolicyBundle) -> PolicyBundle:
        self._bundle = bundle
        return bundle

    async def get_cached_decision(
        self, tenant_id: UUID, command_fingerprint: str
    ) -> PolicyDecision | None:
        return self._decisions.get((tenant_id, command_fingerprint))

    async def insert_policy_decision(self, decision: PolicyDecision) -> PolicyDecision:
        self.policy_decision_command_types.append(decision.command_type)
        self._decisions[(decision.tenant_id, decision.command_fingerprint)] = decision
        return decision


class UnusedConnectionRepository:
    """`mirror_signal`은 항상 `connection_id=None`으로 리스크 게이트를 부른다
    (팔로우 구독에는 거래소 connection 개념이 없다) — 이 저장소가 호출되면
    그 불변이 깨졌다는 뜻이다."""

    async def get_connection(self, connection_id: UUID) -> Any:
        raise AssertionError("connection_repo는 connection_id=None일 때 호출되면 안 된다")

    async def get_latest_health(self, connection_id: UUID) -> Any:
        raise AssertionError("connection_repo는 connection_id=None일 때 호출되면 안 된다")
