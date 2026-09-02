"""Portfolio Mandate repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.mandates.domain.models import (
    MandateRevision,
    PolicyBundle,
    PolicyDecision,
    PortfolioMandate,
)


class MandateRepository(Protocol):
    async def get_mandate(self, tenant_id: UUID) -> PortfolioMandate | None: ...

    async def get_or_create_mandate(self, tenant_id: UUID, subject_id: UUID) -> PortfolioMandate:
        """75번 §1 "one active mandate per subject" — mandate 행 자체는 tenant당
        하나뿐이라 최초 draft 생성 시 없으면 만든다(UNIQUE(tenant_id) 제약이
        경합을 막음)."""
        ...

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None: ...

    async def get_active_revision(self, mandate_id: UUID) -> MandateRevision | None:
        """`portfolio_mandate.active_revision_id`가 가리키는 revision을 그대로
        반환한다 — 이름과 달리 상태가 실제로 `ACTIVE`인지는 걸러내지 않는다.
        `PAUSED`로 전이된 뒤에도 포인터는 그대로 그 revision을 가리키므로(75번
        §2 "PAUSED -> ACTIVE needs fresh policy evaluation"에서 재조회가 아니라
        재평가만 요구하는 것과 같은 이유), 이 메서드는 "mandate가 지금 참조하는
        단 하나의 revision"을 뜻하지 진짜 `state == 'ACTIVE'` 필터가 아니다.
        `evaluate_policy()`처럼 실제 ACTIVE만 써야 하는 호출부는 반환값의
        `.state`를 직접 확인한다."""
        ...

    async def list_revisions(self, mandate_id: UUID) -> list[MandateRevision]: ...

    async def insert_draft_revision(
        self,
        *,
        mandate_id: UUID,
        revision_no: int,
        rules: MandateRevision,
    ) -> MandateRevision:
        """새 DRAFT revision을 append한다(immutable, 75번 §1)."""
        ...

    async def transition_revision_state(
        self,
        revision_id: UUID,
        *,
        expected_state: str,
        new_state: str,
        extra_set_values: dict[str, object] | None = None,
    ) -> MandateRevision:
        """105번 표준의 conditional_update로 상태 전이. 대상이 기대 상태가
        아니면 ConcurrencyConflictError(구현체 책임)."""
        ...

    async def activate_revision(self, mandate_id: UUID, revision_id: UUID) -> MandateRevision:
        """revision을 ACTIVE로 전이하고 동시에 이전 ACTIVE revision을
        SUPERSEDED로, portfolio_mandate.active_revision_id를 갱신한다 — 75번
        §2 "One transaction promotes a revision and supersedes the prior
        revision." 한 트랜잭션 안에서 처리(구현체 책임)."""
        ...

    async def insert_policy_bundle(self, bundle: PolicyBundle) -> PolicyBundle: ...

    async def get_bundle_for_revision(self, revision_id: UUID) -> PolicyBundle | None: ...

    async def insert_policy_decision(self, decision: PolicyDecision) -> PolicyDecision: ...

    async def get_cached_decision(
        self, tenant_id: UUID, command_fingerprint: str
    ) -> PolicyDecision | None:
        """75번 §3 "Denied decisions are cached only for their exact input
        fingerprint and short TTL" — 아직 만료되지 않은 캐시만 반환하는 건
        구현체 책임(evaluated_at + TTL 또는 expires_at 확인)."""
        ...
