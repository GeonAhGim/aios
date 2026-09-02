"""EvaluatePolicy 쿼리(부수효과: PolicyDecision 기록은 있지만 이벤트는 없음).

Spec: AIOSproject 75번 §3 (`EvaluatePolicy`).

다른 bounded context(risk_gate, paper_control 등, 아직 미구현)는 이 함수를
통해서만 mandate 판단을 소비한다 — 71번 §4 Contract ownership.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.foundation.mandates.contracts.v1 import PolicyDecisionView, PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as ContractOutcome
from src.foundation.mandates.domain.models import MandateRevisionState
from src.foundation.mandates.domain.models import PolicyBundle as DomainBundle
from src.foundation.mandates.domain.models import PolicyDecision as DomainDecision
from src.foundation.mandates.domain.models import PolicyEvaluationSubject as DomainSubject
from src.foundation.mandates.domain.models import PolicyOutcome as DomainOutcome
from src.foundation.mandates.domain.rules import (
    compile_rule_hash,
    compiler_version,
    evaluate_policy,
)
from src.foundation.mandates.ports.repository import MandateRepository

DECISION_CACHE_TTL_SECONDS = 30
"""75번 §3 "short TTL" — 같은 tenant/fingerprint 재요청이 몰릴 때(예: UI가
버튼 연타를 막지 못한 경우) 매번 재계산하지 않는다. 30초는 임의값이며,
실제 배포 데이터로 조정 대상."""


class NoActiveMandateError(Exception):
    pass


def _fingerprint(tenant_id: UUID, subject: PolicyEvaluationSubject) -> str:
    payload = json.dumps(
        {"tenant_id": str(tenant_id), **subject.model_dump(mode="json")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decision_to_view(decision: DomainDecision) -> PolicyDecisionView:
    return PolicyDecisionView(
        id=decision.id,
        tenant_id=decision.tenant_id,
        bundle_id=decision.bundle_id,
        command_type=decision.command_type,
        outcome=ContractOutcome(decision.outcome.value),
        reason_codes=list(decision.reason_codes),
        obligations=list(decision.obligations),
        evaluated_at=decision.evaluated_at,
        expires_at=decision.expires_at,
    )


async def evaluate(
    repo: MandateRepository, *, tenant_id: UUID, subject: PolicyEvaluationSubject
) -> PolicyDecisionView:
    fingerprint = _fingerprint(tenant_id, subject)
    cached = await repo.get_cached_decision(tenant_id, fingerprint)
    if cached is not None:
        return _decision_to_view(cached)

    mandate = await repo.get_mandate(tenant_id)
    if mandate is None or mandate.active_revision_id is None:
        raise NoActiveMandateError(str(tenant_id))
    revision = await repo.get_revision(mandate.active_revision_id)
    assert revision is not None  # FK가 보장

    if revision.state == MandateRevisionState.PAUSED:
        outcome, reasons, obligations = (
            DomainOutcome.PAUSE_REQUIRED,
            ["STATE_MANDATE_PAUSED"],
            ["REQUIRE_RISK_GATE"],
        )
    elif revision.state != MandateRevisionState.ACTIVE:
        outcome, reasons, obligations = (
            DomainOutcome.DENY,
            ["STATE_NO_ACTIVE_MANDATE"],
            [],
        )
    else:
        domain_subject = DomainSubject(
            command_type=subject.command_type,
            instrument_exposure_pct=subject.instrument_exposure_pct,
            total_exposure_pct=subject.total_exposure_pct,
            cash_buffer_pct=subject.cash_buffer_pct,
            projected_daily_loss_pct=subject.projected_daily_loss_pct,
            requested_autonomy=(
                None
                if subject.requested_autonomy is None
                else type(revision.allowed_autonomy)(subject.requested_autonomy.value)
            ),
            asset=subject.asset,
        )
        outcome, reasons, obligations = evaluate_policy(revision, domain_subject)

    bundle = await repo.get_bundle_for_revision(revision.id)
    if bundle is None:
        bundle = await repo.insert_policy_bundle(
            DomainBundle(
                id=uuid4(),
                mandate_revision_id=revision.id,
                compiler_version=compiler_version(),
                rule_hash=compile_rule_hash(revision),
                created_at=datetime.now(timezone.utc),
            )
        )

    now = datetime.now(timezone.utc)
    decision = await repo.insert_policy_decision(
        DomainDecision(
            id=uuid4(),
            tenant_id=tenant_id,
            bundle_id=bundle.id,
            command_type=subject.command_type,
            command_fingerprint=fingerprint,
            outcome=outcome,
            reason_codes=tuple(reasons),
            obligations=tuple(obligations),
            evaluated_at=now,
            expires_at=now + timedelta(seconds=DECISION_CACHE_TTL_SECONDS),
        )
    )
    return _decision_to_view(decision)
