"""Table-level tests for src/core/notifications/channel_policy.py (FD-17.2).

Sibling of test_channel_policy.py. That file clears _POLICY_TABLE in an autouse
fixture, so its "known event" assertions never see the real table. These tests
read the real table and pin the FD-17.2 forced-channel whitelist.
"""

import pytest
from pydantic import ValidationError

from src.core.notifications import channel_policy as cp
from src.core.notifications.channel_policy import (
    _DEFAULT_POLICY,
    _POLICY_TABLE,
    ChannelPolicy,
    ChannelRule,
    NotificationChannel,
    get_channel_policy,
)

EMAIL = NotificationChannel.EMAIL
PUSH = NotificationChannel.PUSH
IN_APP = NotificationChannel.IN_APP

EXPECTED_FORCED = {
    "approval.request.created": [EMAIL, PUSH],
    "watchdog.decision.triggered": [EMAIL, PUSH],
    "risk.circuit_breaker.reactivation_requested": [EMAIL, PUSH],
    "security.withdrawal_whitelist.added": [EMAIL, PUSH],
    "execution.safety_block.applied": [IN_APP],
    "risk_profile.match.warned": [IN_APP],
    "marketplace.purchase.requested": [],
    "marketplace.payment.confirmed": [],
    "strategy.verification.completed": [],
    "alert.triggered": [IN_APP],
}


@pytest.mark.parametrize("event_type", sorted(EXPECTED_FORCED))
def test_real_table_forced_channels_match_fd_17_2(event_type):
    """Each registered event forces exactly the FD-17.2 channels."""
    assert get_channel_policy(event_type).forced_channels == EXPECTED_FORCED[event_type]


def test_real_table_covers_every_expected_event():
    """Guards against silent removal of a table entry."""
    assert set(_POLICY_TABLE) == set(EXPECTED_FORCED)


@pytest.mark.parametrize("event_type", sorted(_POLICY_TABLE))
def test_table_rules_have_unique_channels(event_type):
    """A channel must not appear twice in one policy (ambiguous override)."""
    channels = [r.channel for r in _POLICY_TABLE[event_type].rules]
    assert len(channels) == len(set(channels))


# ------------------------------------------------------------------
# Negative tests: whitelist behaviour for unknown / malformed event types
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    [
        "APPROVAL.REQUEST.CREATED",  # case variant must not inherit the forced policy
        "approval.request.created ",  # trailing whitespace
        "approval.request",  # prefix of a registered key
    ],
)
def test_near_miss_event_names_do_not_inherit_forced_policy(event_type):
    """Lookup is exact-match: near-miss names fall back to the safe default."""
    result = get_channel_policy(event_type)
    # We now return a copy of _DEFAULT_POLICY (defensive copy), so check
    # structural equivalence rather than identity.
    assert result.forced_channels == []
    assert result.rules == _DEFAULT_POLICY.rules


def test_default_policy_is_email_only_and_overridable():
    """Fail-safe default: email, user-overridable, nothing forced (FD-17.2)."""
    assert _DEFAULT_POLICY.forced_channels == []
    assert [(r.channel, r.user_overridable) for r in _DEFAULT_POLICY.rules] == [(EMAIL, True)]


def test_unknown_channel_string_rejected():
    """A channel outside the three-member enum cannot be constructed."""
    with pytest.raises(ValidationError):
        ChannelRule(channel="SMS", user_overridable=False)


def test_unknown_channel_in_policy_rules_rejected():
    """ChannelPolicy validates nested rules, so a bad channel fails the whole policy."""
    with pytest.raises(ValidationError):
        ChannelPolicy(rules=[{"channel": "FAX", "user_overridable": True}])


# ------------------------------------------------------------------
# Failure injection: dependency failure inside the lookup
# ------------------------------------------------------------------


def test_lookup_failure_propagates_and_never_returns_permissive_default(monkeypatch):
    """If the table lookup fails, the error surfaces instead of silently returning
    the default. Fail-closed: the caller must not treat a broken lookup as 'no forced'."""

    class BrokenTable(dict):
        def get(self, key, default=None):
            raise RuntimeError("policy store unavailable")

    monkeypatch.setattr(cp, "_POLICY_TABLE", BrokenTable())
    with pytest.raises(RuntimeError, match="policy store unavailable"):
        get_channel_policy("approval.request.created")


# ------------------------------------------------------------------
# Performance budget (perf_budget fixture)
# ------------------------------------------------------------------


def test_lookup_within_budget(perf_budget):
    """Policy lookup is on the notification hot path; keep it sub-millisecond."""
    perf_budget.assert_within(
        lambda: get_channel_policy("approval.request.created"),
        budget_ms=1.0,
        n=5,
        batch=1000,
        label="channel_policy lookup",
    )
