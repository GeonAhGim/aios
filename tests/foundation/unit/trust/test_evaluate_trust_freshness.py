"""FND `application/evaluate_trust_freshness.py` -- mocked failure injection +
perf assertion + gate-red repro (same reasoning as
`tests/foundation/unit/connections/test_revoke_connection.py`, task-4636 style).

`test_accept_and_revoke_consent.py` already covers the happy/DB-integration
path against a real Postgres pool; this file isolates the application layer
behind an in-memory fake so repository failures can be forced on demand and
the reason_code branching asserted without a DB.

# ratchet-allow: fail-closed Protocol stub methods (FakeTrustRepository
# members evaluate_trust_freshness() never calls) raise NotImplementedError so
# an accidental call surfaces immediately instead of returning a silently
# wrong fake value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure

_NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


@dataclass
class FakeTrustRepository:
    disclosure: Disclosure | None
    consent: Consent | None
    disclosure_exc: Exception | None = None
    consent_exc: Exception | None = None
    disclosure_calls: int = 0
    consent_calls: int = 0

    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        self.disclosure_calls += 1
        if self.disclosure_exc is not None:
            raise self.disclosure_exc
        return self.disclosure

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        self.consent_calls += 1
        if self.consent_exc is not None:
            raise self.consent_exc
        return self.consent

    # Unused Protocol members -- not exercised by evaluate_trust_freshness().
    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> tuple[Disclosure, datetime] | None:
        raise NotImplementedError

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        raise NotImplementedError

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        raise NotImplementedError

    async def insert_consent(self, **kwargs: object) -> Consent:
        raise NotImplementedError

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        raise NotImplementedError


def _context() -> TenantContext:
    tenant_id = uuid4()
    return TenantContext(tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=False)


def _disclosure(*, purpose: str, revision: int = 1) -> Disclosure:
    return Disclosure(
        id=uuid4(),
        purpose=purpose,
        revision=revision,
        content_hash="deadbeef",
        published_at=_NOW - timedelta(days=1),
        retired_at=None,
    )


def _consent(
    *,
    tenant_id: UUID,
    purpose: str,
    revision: int = 1,
    state: ConsentState = ConsentState.ACTIVE,
    expires_at: datetime | None = None,
) -> Consent:
    return Consent(
        id=uuid4(),
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=purpose,
        disclosure_id=uuid4(),
        disclosure_revision=revision,
        state=state,
        accepted_at=_NOW - timedelta(hours=1),
        revoked_at=None,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_missing_disclosure_denies_with_not_published_reason() -> None:
    """No disclosure at all -- must report POLICY_DISCLOSURE_NOT_PUBLISHED,
    not POLICY_CONSENT_REQUIRED, even though a consent lookup still runs
    (a "no disclosure" state must not be conflated with "no consent")."""
    context = _context()
    repo = FakeTrustRepository(disclosure=None, consent=None)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_DISCLOSURE_NOT_PUBLISHED"


async def test_no_consent_record_denies_as_consent_required() -> None:
    context = _context()
    disclosure = _disclosure(purpose="p1")
    repo = FakeTrustRepository(disclosure=disclosure, consent=None)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_REQUIRED"


async def test_revoked_consent_denies_as_revoked_not_required() -> None:
    """Distinguishing REVOKED from "never consented" is the whole reason
    `get_latest_consent` (not `get_active_consent`) is used -- see the
    module's own docstring."""
    context = _context()
    disclosure = _disclosure(purpose="p1")
    consent = _consent(tenant_id=context.tenant_id, purpose="p1", state=ConsentState.REVOKED)
    repo = FakeTrustRepository(disclosure=disclosure, consent=consent)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_REVOKED"


async def test_stale_disclosure_revision_denies() -> None:
    context = _context()
    disclosure = _disclosure(purpose="p1", revision=2)
    consent = _consent(tenant_id=context.tenant_id, purpose="p1", revision=1)
    repo = FakeTrustRepository(disclosure=disclosure, consent=consent)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_STALE_REVISION"


async def test_expired_consent_denies_even_though_still_active() -> None:
    context = _context()
    disclosure = _disclosure(purpose="p1")
    consent = _consent(
        tenant_id=context.tenant_id,
        purpose="p1",
        expires_at=_NOW - timedelta(seconds=1),
    )
    repo = FakeTrustRepository(disclosure=disclosure, consent=consent)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_EXPIRED"


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_disclosure_lookup_failure_propagates_and_skips_consent_lookup() -> None:
    context = _context()
    repo = FakeTrustRepository(
        disclosure=None,
        consent=None,
        disclosure_exc=ConnectionResetError("simulated connection drop"),
    )

    with pytest.raises(ConnectionResetError):
        await evaluate_trust_freshness(repo, context, purpose="p1")

    assert repo.consent_calls == 0


async def test_consent_lookup_failure_propagates() -> None:
    context = _context()
    disclosure = _disclosure(purpose="p1")
    repo = FakeTrustRepository(
        disclosure=disclosure,
        consent=None,
        consent_exc=TimeoutError("simulated query timeout"),
    )

    with pytest.raises(TimeoutError):
        await evaluate_trust_freshness(repo, context, purpose="p1")


# ---------------------------------------------------------------------------
# success path (D2)
# ---------------------------------------------------------------------------


async def test_active_matching_consent_is_fresh() -> None:
    context = _context()
    disclosure = _disclosure(purpose="p1")
    consent = _consent(tenant_id=context.tenant_id, purpose="p1")
    repo = FakeTrustRepository(disclosure=disclosure, consent=consent)

    decision = await evaluate_trust_freshness(repo, context, purpose="p1")

    assert decision.is_fresh is True
    assert decision.reason_code is None
    assert decision.tenant_id == context.tenant_id
    assert decision.purpose == "p1"


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves the disclosure-first short-circuit is
# load-bearing for the reason_code taxonomy.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_disclosure_short_circuit_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    repo = FakeTrustRepository(disclosure=None, consent=None)

    # green: guard active -- missing disclosure reports
    # POLICY_DISCLOSURE_NOT_PUBLISHED.
    decision = await evaluate_trust_freshness(repo, context, purpose="p1")
    assert decision.reason_code == "POLICY_DISCLOSURE_NOT_PUBLISHED"

    # red repro: neutralize the `is_consent_fresh` call this module relies on
    # to reject a REVOKED consent, by forcing it to always report fresh.
    # Without that call being load-bearing, a REVOKED consent would be
    # reported as `is_fresh=True` -- the exact failure this module exists to
    # prevent (73 §3.2 "an expired/revoked consent is immediately void").
    import src.foundation.trust.application.evaluate_trust_freshness as target

    monkeypatch.setattr(target, "is_consent_fresh", lambda *args, **kwargs: True)

    disclosure = _disclosure(purpose="p1")
    revoked_consent = _consent(
        tenant_id=context.tenant_id, purpose="p1", state=ConsentState.REVOKED
    )
    repo2 = FakeTrustRepository(disclosure=disclosure, consent=revoked_consent)

    decision2 = await evaluate_trust_freshness(repo2, context, purpose="p1")
    assert decision2.is_fresh is True  # the bug this repro demonstrates


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_evaluate_trust_freshness_perf_budget_p95_latency() -> None:
    """No published per-axis budget covers Trust Core queries specifically
    (ADR-2026-09-09-C Decision 1's table); pin the same order of magnitude as
    the closest published read-query budget, served purely in-memory here."""
    context = _context()
    disclosure = _disclosure(purpose="p1")
    consent = _consent(tenant_id=context.tenant_id, purpose="p1")
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        repo = FakeTrustRepository(disclosure=disclosure, consent=consent)
        start = time.perf_counter()
        await evaluate_trust_freshness(repo, context, purpose="p1")
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, f"evaluate_trust_freshness p95 latency {p95:.3f}ms exceeded 50ms budget"
