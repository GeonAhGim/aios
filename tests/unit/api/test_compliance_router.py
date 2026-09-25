"""L4_compliance_and_regulatory_v1.0.md#9 CM-17 — unit tests for
`src/api/routers/foundation/compliance.py`.

No FastAPI TestClient/DB here — the router's two handlers are thin
(auth/DI/command-invocation only, 71번 §6), so calling them directly with a
fake `MandateRepository` and a fake `User` exercises the same code path
without I/O. Cross-tenant/404 isomorphism (task-2618, CM-17 DoD) is the
behavior under test: a missing decision and a decision owned by a
different tenant must raise the exact same exception type so the API layer
cannot be used to enumerate other tenants' decision ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.api.routers.foundation.compliance import get_decision, get_mandate_status
from src.foundation.mandates.application.explain import ExplainDecisionNotFoundError
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyBundle,
    PolicyDecision,
    PolicyOutcome,
)
from src.foundation.mandates.domain.rules import compile_rule_hash
from src.services.auth_service import User

_NOW = datetime(2026, 1, 5, 5, 0, 0, tzinfo=timezone.utc)


def _user(user_id: UUID) -> User:
    return User(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _revision() -> MandateRevision:
    return MandateRevision(
        id=uuid4(),
        mandate_id=uuid4(),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=20.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=(),
    )


def _bundle(revision: MandateRevision) -> PolicyBundle:
    return PolicyBundle(
        id=uuid4(),
        mandate_revision_id=revision.id,
        compiler_version="v1",
        rule_hash=compile_rule_hash(revision),
        created_at=_NOW,
    )


def _decision(tenant_id: UUID, bundle: PolicyBundle) -> PolicyDecision:
    return PolicyDecision(
        id=uuid4(),
        tenant_id=tenant_id,
        bundle_id=bundle.id,
        command_type="submit_order",
        command_fingerprint="a" * 64,
        outcome=PolicyOutcome.DENY,
        reason_codes=("POLICY_MAX_TOTAL_EXPOSURE",),
        obligations=("REQUIRE_RISK_GATE",),
        evaluated_at=_NOW,
        expires_at=None,
    )


@dataclass
class _FakeMandateRepo:
    """`get_decision`/`get_mandate_status`가 실제로 쓰는 메서드만 흉내낸다."""

    decisions: dict[UUID, PolicyDecision] = field(default_factory=dict)
    bundles: dict[UUID, PolicyBundle] = field(default_factory=dict)
    revisions: dict[UUID, MandateRevision] = field(default_factory=dict)

    async def get_policy_decision(self, decision_id: UUID) -> PolicyDecision | None:
        return self.decisions.get(decision_id)

    async def get_bundle(self, bundle_id: UUID) -> PolicyBundle | None:
        return self.bundles.get(bundle_id)

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        return self.revisions.get(revision_id)

    async def get_mandate(self, tenant_id: UUID, portfolio_id: UUID | None = None) -> None:
        return None


def _repo_with(
    revision: MandateRevision, bundle: PolicyBundle, decision: PolicyDecision
) -> _FakeMandateRepo:
    return _FakeMandateRepo(
        decisions={decision.id: decision},
        bundles={bundle.id: bundle},
        revisions={revision.id: revision},
    )


async def test_get_decision_returns_explained_decision_for_owning_tenant() -> None:
    tenant_id = uuid4()
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(tenant_id, bundle)
    repo = _repo_with(revision, bundle, decision)

    response = await get_decision(decision.id, user=_user(tenant_id), repo=repo)

    assert response.data.decision_id == decision.id
    assert response.data.verdict == ComplianceVerdict.DENY
    assert response.data.bundle_version == bundle.rule_hash


async def test_get_decision_rejects_unknown_id_with_defined_error() -> None:
    repo = _FakeMandateRepo()

    with pytest.raises(ExplainDecisionNotFoundError):
        await get_decision(uuid4(), user=_user(uuid4()), repo=repo)


async def test_get_decision_cross_tenant_access_raises_same_error_as_missing() -> None:
    """CM-17 DoD "404 동형" — 존재하지만 다른 tenant 소유인 decision과 아예
    존재하지 않는 decision이 API 관점에서 구분되면 안 된다."""
    owner_id = uuid4()
    stranger_id = uuid4()
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(owner_id, bundle)
    repo = _repo_with(revision, bundle, decision)

    with pytest.raises(ExplainDecisionNotFoundError):
        await get_decision(decision.id, user=_user(stranger_id), repo=repo)


async def test_mandate_status_reports_no_mandate_for_fresh_tenant() -> None:
    repo = _FakeMandateRepo()

    response = await get_mandate_status(user=_user(uuid4()), repo=repo)

    assert response.data.active_revision is None
    assert response.data.pending_revision is None
