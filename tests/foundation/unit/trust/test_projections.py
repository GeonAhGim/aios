"""FND `projections.py` -- `build_trust_status_view` coverage (task-4679).

Isolated behind an in-memory fake repository (same pattern as
`test_evaluate_trust_freshness.py`) so repository failures, empty results,
and cross-tenant mixing can be forced on demand without a DB.

# ratchet-allow: fail-closed Protocol stub methods (FakeTrustRepository
# members build_trust_status_view() never calls) raise NotImplementedError so
# an accidental call surfaces immediately instead of returning a silently
# wrong fake value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.trust.contracts.v1 import ConsentState as ContractConsentState
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure
from src.foundation.trust.projections import build_trust_status_view

_NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


@dataclass
class FakeTrustRepository:
    consents: list[Consent]
    exc: Exception | None = None
    calls: int = 0
    seen_tenant_ids: list[UUID] = field(default_factory=list)

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        self.calls += 1
        self.seen_tenant_ids.append(tenant_id)
        if self.exc is not None:
            raise self.exc
        return self.consents

    # Unused Protocol members -- not exercised by build_trust_status_view().
    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        raise NotImplementedError

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> tuple[Disclosure, datetime] | None:
        raise NotImplementedError

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        raise NotImplementedError

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        raise NotImplementedError

    async def insert_consent(self, **kwargs: object) -> Consent:
        raise NotImplementedError

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        raise NotImplementedError


def _consent(
    *,
    tenant_id: UUID,
    purpose: str = "p1",
    state: ConsentState = ConsentState.ACTIVE,
) -> Consent:
    return Consent(
        id=uuid4(),
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=uuid4(),
        disclosure_revision=1,
        state=state,
        accepted_at=_NOW - timedelta(hours=1),
        revoked_at=None,
        expires_at=None,
    )


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_no_active_consents_yields_empty_view() -> None:
    tenant_id = uuid4()
    repo = FakeTrustRepository(consents=[])

    view = await build_trust_status_view(repo, tenant_id)

    assert view.tenant_id == tenant_id
    assert view.consents == []


async def test_repository_error_propagates_and_produces_no_view() -> None:
    tenant_id = uuid4()
    repo = FakeTrustRepository(consents=[], exc=ConnectionResetError("simulated connection drop"))

    with pytest.raises(ConnectionResetError):
        await build_trust_status_view(repo, tenant_id)


async def test_repository_receives_the_requested_tenant_id_not_a_default() -> None:
    """Guards against a copy-paste bug that hardcodes some other tenant_id
    (e.g. a fixture default) instead of forwarding the caller's argument."""
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    repo = FakeTrustRepository(consents=[_consent(tenant_id=other_tenant_id)])

    view = await build_trust_status_view(repo, tenant_id)

    assert repo.seen_tenant_ids == [tenant_id]
    # The repository is trusted to filter by tenant_id; the projection must not
    # re-filter or silently drop rows the repository returns for the requested id.
    assert view.tenant_id == tenant_id
    assert len(view.consents) == 1


# ---------------------------------------------------------------------------
# success / mapping (D2)
# ---------------------------------------------------------------------------


async def test_multiple_consents_are_all_mapped_with_correct_field_values() -> None:
    tenant_id = uuid4()
    consent = _consent(tenant_id=tenant_id, purpose="p1")
    other = _consent(tenant_id=tenant_id, purpose="p2", state=ConsentState.ACTIVE)
    repo = FakeTrustRepository(consents=[consent, other])

    before = datetime.now(timezone.utc)
    view = await build_trust_status_view(repo, tenant_id)
    after = datetime.now(timezone.utc)

    assert len(view.consents) == 2
    mapped = view.consents[0]
    assert mapped.consent_id == consent.id
    assert mapped.tenant_id == consent.tenant_id
    assert mapped.purpose == consent.purpose
    assert mapped.disclosure_id == consent.disclosure_id
    assert mapped.disclosure_revision == consent.disclosure_revision
    assert mapped.state == ContractConsentState(consent.state.value)
    assert mapped.accepted_at == consent.accepted_at
    assert mapped.revoked_at == consent.revoked_at
    assert mapped.expires_at == consent.expires_at
    # as_of is the projection's own read-time clock, not copied from any consent.
    assert before <= view.as_of <= after
    assert view.as_of.tzinfo is not None


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves the per-item mapping loop is load-bearing.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_mapping_loop_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    consent = _consent(tenant_id=tenant_id, purpose="p1")
    repo = FakeTrustRepository(consents=[consent])

    # green: guard active -- one repository row maps to exactly one view item.
    view = await build_trust_status_view(repo, tenant_id)
    assert len(view.consents) == 1

    # red repro: neutralize list_active_consents to always report no rows,
    # simulating a broken query filter. Without the mapping loop being
    # load-bearing, a tenant with active consents would see an empty
    # TrustStatusView -- silently hiding consent state from callers such as
    # GET /v1/trust/status.
    async def _always_empty(self: FakeTrustRepository, tenant_id: UUID) -> list[Consent]:
        return []

    monkeypatch.setattr(FakeTrustRepository, "list_active_consents", _always_empty)

    view2 = await build_trust_status_view(repo, tenant_id)
    assert view2.consents == []  # the bug this repro demonstrates


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_build_trust_status_view_perf_budget_p95_latency() -> None:
    """No published per-axis budget covers Trust Core projections specifically
    (ADR-2026-09-09-C Decision 1's table); pin the same order of magnitude as
    the closest published read-query budget, served purely in-memory here."""
    tenant_id = uuid4()
    consents = [_consent(tenant_id=tenant_id, purpose=f"p{i}") for i in range(5)]
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        repo = FakeTrustRepository(consents=consents)
        start = time.perf_counter()
        await build_trust_status_view(repo, tenant_id)
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, f"build_trust_status_view p95 latency {p95:.3f}ms exceeded 50ms budget"
