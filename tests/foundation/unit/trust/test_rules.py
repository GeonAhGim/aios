"""Trust Core domain/rules.py 단위테스트 — DB 없이 순수 함수만 검증."""
from datetime import datetime, timedelta, timezone
from time import perf_counter_ns
from uuid import uuid4

import pytest

from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure
from src.foundation.trust.domain.rules import (
    freshness_denial_reason,
    is_consent_fresh,
    is_disclosure_acceptable,
)

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _disclosure(*, revision: int = 1, retired_at: datetime | None = None) -> Disclosure:
    return Disclosure(
        id=uuid4(),
        purpose="terms_of_service",
        revision=revision,
        content_hash="hash",
        published_at=NOW - timedelta(days=30),
        retired_at=retired_at,
    )


def _consent(
    *,
    disclosure: Disclosure,
    state: ConsentState = ConsentState.ACTIVE,
    expires_at: datetime | None = None,
) -> Consent:
    return Consent(
        id=uuid4(),
        tenant_id=uuid4(),
        subject_id=uuid4(),
        purpose=disclosure.purpose,
        disclosure_id=disclosure.id,
        disclosure_revision=disclosure.revision,
        state=state,
        accepted_at=NOW - timedelta(days=1),
        revoked_at=None,
        expires_at=expires_at,
    )


def test_disclosure_without_retired_at_is_acceptable():
    assert is_disclosure_acceptable(_disclosure(), now=NOW) is True


def test_retired_disclosure_is_not_acceptable():
    disclosure = _disclosure(retired_at=NOW - timedelta(days=1))
    assert is_disclosure_acceptable(disclosure, now=NOW) is False


def test_disclosure_retiring_in_the_future_is_still_acceptable():
    disclosure = _disclosure(retired_at=NOW + timedelta(days=1))
    assert is_disclosure_acceptable(disclosure, now=NOW) is True


def test_no_consent_is_not_fresh():
    disclosure = _disclosure()
    assert is_consent_fresh(None, required_disclosure=disclosure, now=NOW) is False
    assert freshness_denial_reason(None, required_disclosure=disclosure, now=NOW) == (
        "POLICY_CONSENT_REQUIRED"
    )


def test_active_consent_matching_current_revision_is_fresh():
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure)
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is True
    assert freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW) is None


def test_revoked_consent_is_not_fresh():
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure, state=ConsentState.REVOKED)
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False
    assert freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW) == (
        "POLICY_CONSENT_REVOKED"
    )


def test_consent_for_older_disclosure_revision_is_not_fresh():
    old_disclosure = _disclosure(revision=1)
    new_disclosure = _disclosure(revision=2)
    consent = _consent(disclosure=old_disclosure)  # revision=1 동의

    assert is_consent_fresh(consent, required_disclosure=new_disclosure, now=NOW) is False
    assert freshness_denial_reason(consent, required_disclosure=new_disclosure, now=NOW) == (
        "POLICY_CONSENT_STALE_REVISION"
    )


def test_expired_consent_is_not_fresh_even_if_state_still_active():
    """73번 §3.2 — 백그라운드 만료 처리가 늦어도 expires_at을 넘으면 즉시 무효."""
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure, expires_at=NOW - timedelta(seconds=1))

    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False
    assert freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW) == (
        "POLICY_CONSENT_EXPIRED"
    )


def test_consent_expiring_exactly_now_is_not_fresh():
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure, expires_at=NOW)
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False


def test_consent_with_future_expiry_is_fresh():
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure, expires_at=NOW + timedelta(days=1))
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is True


def test_consent_for_different_purpose_is_not_fresh():
    disclosure = _disclosure()
    other_purpose_disclosure = Disclosure(
        id=uuid4(),
        purpose="marketing_communications",
        revision=1,
        content_hash="hash",
        published_at=NOW - timedelta(days=30),
        retired_at=None,
    )
    consent = _consent(disclosure=other_purpose_disclosure)

    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False


def test_negative_disclosure_retired_exactly_now_is_rejected():
    disclosure = _disclosure(retired_at=NOW)
    assert is_disclosure_acceptable(disclosure, now=NOW) is False
    assert is_disclosure_acceptable(disclosure, now=NOW - timedelta(microseconds=1)) is True


def test_negative_future_revision_consent_is_rejected():
    disclosure = _disclosure(revision=1)
    consent = _consent(disclosure=_disclosure(revision=2))
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False
    assert freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW) == (
        "POLICY_CONSENT_STALE_REVISION"
    )


def test_negative_revoked_stale_expired_consent_preserves_revocation_reason():
    disclosure = _disclosure(revision=2)
    consent = _consent(
        disclosure=_disclosure(revision=1),
        state=ConsentState.REVOKED,
        expires_at=NOW - timedelta(seconds=1),
    )
    # I-07: multiple failures must never turn a domain denial into approval.
    assert is_consent_fresh(consent, required_disclosure=disclosure, now=NOW) is False
    assert freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW) == (
        "POLICY_CONSENT_REVOKED"
    )


@pytest.mark.parametrize("evaluate", [is_consent_fresh, freshness_denial_reason])
def test_failure_injection_expiry_access_error_propagates(monkeypatch, evaluate):
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure)
    failure = RuntimeError("injected expiry access failure")

    def fail_expiry_access(self):
        raise failure

    monkeypatch.setattr(Consent, "expires_at", property(fail_expiry_access), raising=False)
    with pytest.raises(RuntimeError, match="injected expiry access failure") as caught:
        evaluate(consent, required_disclosure=disclosure, now=NOW)
    assert caught.value is failure


def test_consent_gate_performance_p99_under_five_ms():
    """ADR-2026-09-09-C D1: pre-trade policy gate p99 budget is 5 ms."""
    disclosure = _disclosure()
    consent = _consent(disclosure=disclosure, expires_at=NOW)
    samples = []
    for _ in range(1000):
        started = perf_counter_ns()
        fresh = is_consent_fresh(consent, required_disclosure=disclosure, now=NOW)
        reason = freshness_denial_reason(consent, required_disclosure=disclosure, now=NOW)
        samples.append(perf_counter_ns() - started)
        assert fresh is False
        assert reason == "POLICY_CONSENT_EXPIRED"
    assert sorted(samples)[989] < 5_000_000
