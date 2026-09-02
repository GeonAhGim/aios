"""CorrectStatement 커맨드 — `CORRECTED` 리비전(prior ref, delta, reason).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49), 81번
§2 "If upstream correction occurs, create CORRECTED revision with prior
ref, delta, reason ... never rewrite statement history."

원본 행은 절대 건드리지 않는다(WORM) — 이 커맨드는 최신 리비전의 값을
그대로 승계한 새 행을 추가하고 `reason`을 감사 이벤트와 `limitations`에
남긴다. 실제 입력 재조회·재계산(economics delta)은 이 리프의 스콥이 아니다
— compute_statement.py를 다시 호출해 같은 기간의 새 리비전을 만드는 건
호출부(라우터) 책임이고, 이 커맨드는 "그 결과를 CORRECTED로 표시하고
이전 리비전과 연결한다"는 관리적 절차만 담당한다(100줄 상한, SCAFFOLD)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.application.statement_projection import statement_to_view
from src.foundation.performance.contracts.v1 import PerformanceStatementView
from src.foundation.performance.domain.models import StatementState
from src.foundation.performance.domain.rules import next_revision
from src.foundation.performance.ports.repository import PerformanceRepository


class StatementNotFoundError(Exception):
    pass


class CrossTenantStatementAccessError(Exception):
    """72번 에러 taxonomy `AUTH_PERFORMANCE_SCOPE_DENIED` — 호출부가 403으로
    매핑한다(get_statement.py와 동일 원칙 — 81번 §3이 이 코드를 404와
    별개로 명명한다)."""

    def __init__(self, statement_id: UUID) -> None:
        super().__init__(f"AUTH_PERFORMANCE_SCOPE_DENIED: {statement_id}")
        self.reason_code = "AUTH_PERFORMANCE_SCOPE_DENIED"


async def correct_statement(
    repo: PerformanceRepository,
    evidence_repo: AuditEventRepository | None,
    *,
    tenant_id: UUID,
    statement_id: UUID,
    reason: str,
    trace_id: UUID,
) -> PerformanceStatementView:
    original = await repo.get_statement(statement_id)
    if original is None:
        raise StatementNotFoundError(str(statement_id))
    if original.tenant_id != tenant_id:
        raise CrossTenantStatementAccessError(statement_id)

    latest = await repo.get_latest_statement(
        tenant_id=tenant_id,
        scope=original.scope,
        scope_ref=original.scope_ref,
        period_start=original.period_start,
        period_end=original.period_end,
        methodology_version=original.methodology_version,
    ) or original

    reason_note = f"CORRECTED(prior={latest.id}): {reason}"
    corrected_id = uuid4()

    evidence_refs = latest.evidence_refs
    if evidence_repo is not None:
        event = await record_command_event(
            evidence_repo,
            tenant_id=tenant_id,
            aggregate_type="performance_statement",
            aggregate_id=corrected_id,
            action="performance.statement_corrected.v1",
            actor_subject_id=tenant_id,
            payload={
                "prior_statement_id": str(latest.id),
                "reason": reason,
                "trace_id": str(trace_id),
            },
        )
        evidence_refs = (*latest.evidence_refs, f"audit:{event.id}")

    corrected = replace(
        latest,
        id=corrected_id,
        as_of=datetime.now(timezone.utc),
        state=StatementState.CORRECTED,
        revision_no=next_revision(latest.revision_no),
        prior_statement_id=latest.id,
        limitations=(*latest.limitations, reason_note),
        evidence_refs=evidence_refs,
    )
    saved = await repo.insert_statement(corrected)
    return statement_to_view(saved)
