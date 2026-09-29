"""Task-8417: Trust consent gate regression tests (INVARIANTS I-07/I-10)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import perf_counter
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure
from src.foundation.trust.domain.rules import is_consent_fresh
from src.foundation.trust.ports.repository import TrustRepository


@pytest.fixture
def consent_gate():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    tenant_id = uuid4()
    context = TenantContext(tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=False)
    disclosure = Disclosure(
        id=uuid4(), purpose="terms", revision=2, content_hash="test-hash",
        published_at=now - timedelta(days=1), retired_at=None,
    )
    consent = Consent(
        id=uuid4(), tenant_id=tenant_id, subject_id=tenant_id, purpose="terms",
        disclosure_id=disclosure.id, disclosure_revision=2, state=ConsentState.ACTIVE,
        accepted_at=now, revoked_at=None, expires_at=None,
    )
    repo = AsyncMock(spec=TrustRepository)
    repo.get_active_disclosure.return_value = disclosure
    repo.get_latest_consent.return_value = consent
    return now, context, disclosure, consent, repo


async def test_negative_revoked_consent_denied_through_application(consent_gate):
    now, context, _, consent, repo = consent_gate
    repo.get_latest_consent.return_value = replace(
        consent, state=ConsentState.REVOKED, revoked_at=now,
    )
    decision = await evaluate_trust_freshness(repo, context, purpose="terms")
    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_REVOKED"
    repo.get_latest_consent.assert_awaited_once_with(context.tenant_id, "terms")


async def test_negative_future_revision_is_not_current_consent(consent_gate):
    _, context, _, consent, repo = consent_gate
    repo.get_latest_consent.return_value = replace(consent, disclosure_revision=3)
    decision = await evaluate_trust_freshness(repo, context, purpose="terms")
    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_STALE_REVISION"


async def test_negative_expiry_boundary_denied_through_application(consent_gate, monkeypatch):
    now, context, _, consent, repo = consent_gate

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(
        "src.foundation.trust.application.evaluate_trust_freshness.datetime", FrozenDatetime,
    )
    repo.get_latest_consent.return_value = replace(consent, expires_at=now)
    decision = await evaluate_trust_freshness(repo, context, purpose="terms")
    assert decision.is_fresh is False
    assert decision.reason_code == "POLICY_CONSENT_EXPIRED"
    repo.get_latest_consent.return_value = replace(
        consent, expires_at=now + timedelta(microseconds=1),
    )
    assert (await evaluate_trust_freshness(repo, context, purpose="terms")).is_fresh is True


async def test_failure_injection_disclosure_timeout_stops_evaluation(consent_gate, monkeypatch):
    _, context, _, _, repo = consent_gate
    error = TimeoutError("injected disclosure timeout")
    lookup = AsyncMock(side_effect=error)
    monkeypatch.setattr(repo, "get_active_disclosure", lookup)
    with pytest.raises(TimeoutError) as raised:
        await evaluate_trust_freshness(repo, context, purpose="terms")
    assert raised.value is error
    lookup.assert_awaited_once_with("terms")
    repo.get_latest_consent.assert_not_awaited()


async def test_red_gate_reproduction_detects_bypassed_domain_guard(consent_gate, monkeypatch):
    _, context, _, consent, repo = consent_gate
    repo.get_latest_consent.return_value = replace(consent, state=ConsentState.REVOKED)

    async def assert_denied():
        decision = await evaluate_trust_freshness(repo, context, purpose="terms")
        assert decision.is_fresh is False, "revoked consent allowed"

    await assert_denied()
    with monkeypatch.context() as patch:
        patch.setattr(
            "src.foundation.trust.application.evaluate_trust_freshness.is_consent_fresh",
            lambda *args, **kwargs: True,
        )
        with pytest.raises(AssertionError, match="revoked consent allowed"):
            await assert_denied()
    await assert_denied()


@pytest.mark.perf
def test_consent_domain_gate_p99_performance_budget(consent_gate):
    """ADR-2026-09-09-C: domain gate stays within pre-trade p99 5ms budget."""
    now, _, disclosure, consent, _ = consent_gate
    revoked = replace(consent, state=ConsentState.REVOKED)
    samples = []
    for _ in range(200):
        start = perf_counter()
        assert is_consent_fresh(consent, required_disclosure=disclosure, now=now) is True
        assert is_consent_fresh(revoked, required_disclosure=disclosure, now=now) is False
        samples.append((perf_counter() - start) * 1000)
    assert sorted(samples)[197] < 5.0
