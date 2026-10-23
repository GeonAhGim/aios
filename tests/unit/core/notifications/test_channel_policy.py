"""Tests for src/core/notifications/channel_policy.py

Coverage targets:
  - NotificationChannel enum (3 members, invalid value)
  - ChannelRule / ChannelPolicy model instantiation
  - ChannelPolicy.forced_channels (various user_overridable combos)
  - get_channel_policy() edge cases (empty string, None, special chars)
  - Failure injection (monkeypatch _POLICY_TABLE.get → KeyError / TypeError)
"""

import pytest
from pydantic import ValidationError

from src.core.notifications.channel_policy import (
    _DEFAULT_POLICY,
    _POLICY_TABLE,
    ChannelPolicy,
    ChannelRule,
    NotificationChannel,
    get_channel_policy,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


# NOTE: The autouse fixture below clears _POLICY_TABLE so that tests in this
# file never accidentally depend on the real table contents.  The sibling file
# test_channel_policy_table.py exercises the real table with pinned assertions.
# See review finding #1 (medium) — test_known_event_returns_table_entry and
# test_all_table_events were asserting over an empty dict and never caught
# regressions when table entries were removed.


@pytest.fixture(autouse=True)
def _clear_table():
    """Each test starts with a clean _POLICY_TABLE."""
    original = dict(_POLICY_TABLE)
    _POLICY_TABLE.clear()
    yield
    _POLICY_TABLE.clear()
    _POLICY_TABLE.update(original)


# ------------------------------------------------------------------
# NotificationChannel enum direct tests
# ------------------------------------------------------------------


class TestNotificationChannelEnum:
    """Verify NotificationChannel enum has three constants."""

    def test_email_member(self):
        assert NotificationChannel.EMAIL.value == "EMAIL"

    def test_push_member(self):
        assert NotificationChannel.PUSH.value == "PUSH"

    def test_in_app_member(self):
        assert NotificationChannel.IN_APP.value == "IN_APP"

    def test_three_members(self):
        assert len(NotificationChannel) == 3

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            NotificationChannel("bogus")


# ------------------------------------------------------------------
# ChannelRule / ChannelPolicy 모델 인스턴스화
# ------------------------------------------------------------------


class TestChannelRuleModel:
    """ChannelRule 생성 및 user_overridable 속성."""

    def test_user_overridable_true(self):
        rule = ChannelRule(
            channel=NotificationChannel.EMAIL,
            user_overridable=True,
        )
        assert rule.user_overridable is True

    def test_user_overridable_explicit_false(self):
        rule = ChannelRule(
            channel=NotificationChannel.PUSH,
            user_overridable=False,
        )
        assert rule.user_overridable is False

    def test_missing_user_overridable_raises_validation(self):
        """user_overridable is required — raises ValidationError on creation."""
        with pytest.raises(ValidationError):
            ChannelRule(channel=NotificationChannel.EMAIL)


class TestChannelPolicyModel:
    """Verify ChannelPolicy instantiation."""

    def test_create_with_rules(self):
        rules = [
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=True),
        ]
        policy = ChannelPolicy(rules=rules)
        assert len(policy.rules) == 2

    def test_empty_rules_list(self):
        policy = ChannelPolicy()
        assert policy.rules == []

    def test_default_rules_is_empty_list(self):
        policy = ChannelPolicy()
        assert policy.rules == []


# ------------------------------------------------------------------
# ChannelPolicy.forced_channels property
# ------------------------------------------------------------------


class TestForcedChannels:
    """forced_channels returns only channels where user_overridable=False."""

    def test_all_user_overridable_false(self):
        rules = [
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=False),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
        ]
        policy = ChannelPolicy(rules=rules)
        assert policy.forced_channels == [NotificationChannel.EMAIL, NotificationChannel.PUSH]

    def test_all_user_overridable_true(self):
        rules = [
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=True),
        ]
        policy = ChannelPolicy(rules=rules)
        assert policy.forced_channels == []

    def test_mixed_user_overridable(self):
        rules = [
            ChannelRule(channel=NotificationChannel.EMAIL, user_overridable=True),
            ChannelRule(channel=NotificationChannel.PUSH, user_overridable=False),
            ChannelRule(channel=NotificationChannel.IN_APP, user_overridable=True),
        ]
        policy = ChannelPolicy(rules=rules)
        assert policy.forced_channels == [NotificationChannel.PUSH]

    def test_empty_rules(self):
        policy = ChannelPolicy()
        assert policy.forced_channels == []

    def test_single_rule_user_overridable_false(self):
        policy = ChannelPolicy(
            rules=[ChannelRule(channel=NotificationChannel.IN_APP, user_overridable=False)]
        )
        assert policy.forced_channels == [NotificationChannel.IN_APP]


# ------------------------------------------------------------------
# get_channel_policy() boundary values and negatives
# ------------------------------------------------------------------


class TestGetChannelPolicyEdgeCases:
    """get_channel_policy() with invalid inputs and boundary values."""

    def test_known_event_returns_table_entry(self):
        result = get_channel_policy("approval.request.created")
        assert isinstance(result, ChannelPolicy)
        assert len(result.rules) == 1

    def test_unknown_event_returns_default(self):
        result = get_channel_policy("unknown.event.type")
        assert result == _DEFAULT_POLICY

    def test_empty_string_event_type(self):
        """Empty string is not in the table, so returns _DEFAULT_POLICY."""
        result = get_channel_policy("")
        assert result == _DEFAULT_POLICY

    def test_none_event_type_returns_default(self):
        """None in dict.get(None) → None → _DEFAULT_POLICY."""
        result = get_channel_policy(None)
        assert result == _DEFAULT_POLICY

    def test_special_characters_event_type(self):
        """Special character event type does not match table → _DEFAULT_POLICY."""
        result = get_channel_policy("event;DROP TABLE;--")
        assert result == _DEFAULT_POLICY

    def test_numeric_event_type(self):
        """Numeric event type does not match table → _DEFAULT_POLICY."""
        result = get_channel_policy("12345")
        assert result == _DEFAULT_POLICY

    def test_all_table_events_return_channel_policy(self):
        """All events registered in table return ChannelPolicy."""
        for event_type in _POLICY_TABLE:
            result = get_channel_policy(event_type)
            assert isinstance(result, ChannelPolicy)


# ------------------------------------------------------------------
# Failure injection (monkeypatch)
# ------------------------------------------------------------------


class TestFailureInjection:
    """Exception injection tests for dependency failures."""

    def test_policy_table_get_raises_key_error_propagates(self, monkeypatch):
        """When _POLICY_TABLE.get() raises KeyError, get_channel_policy
        propagates it (no try/except)."""

        # Replace the module-level _POLICY_TABLE entirely with a wrapper dict
        class FailingDict(dict):
            def get(self, key, default=None):
                raise KeyError(key)

        monkeypatch.setattr(
            "src.core.notifications.channel_policy._POLICY_TABLE",
            FailingDict(),
        )

        # get_channel_policy without try/except propagates KeyError
        with pytest.raises(KeyError):
            get_channel_policy("boom_event")

    def test_policy_table_raises_type_error(self, monkeypatch):
        """When _POLICY_TABLE.get() raises TypeError, exception is propagated."""

        class RaisingDict(dict):
            def get(self, key, default=None):
                raise TypeError("simulated storage failure")

        monkeypatch.setattr(
            "src.core.notifications.channel_policy._POLICY_TABLE",
            RaisingDict(),
        )

        with pytest.raises(TypeError):
            get_channel_policy("any_event")

    def test_unknown_event_returns_copy_not_singleton(self):
        """get_channel_policy must return a deep copy of _DEFAULT_POLICY so
        callers cannot mutate the module-level singleton (review finding #2, low)."""
        result = get_channel_policy("unknown.event")
        assert result is not _DEFAULT_POLICY
        # Mutating the result's rules must not affect _DEFAULT_POLICY
        result.rules.clear()
        assert len(_DEFAULT_POLICY.rules) == 1
        assert _DEFAULT_POLICY.rules[0].channel == NotificationChannel.EMAIL
