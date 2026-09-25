"""Tests for src/core/notifications/channel_policy.py

Coverage targets:
  - NotificationChannel enum (3 members, invalid value)
  - ChannelRule / ChannelPolicy model instantiation
  - ChannelPolicy.forced_channels (various user_overridable combos)
  - get_channel_policy() edge cases (empty string, None, special chars)
  - Failure injection (monkeypatch _POLICY_TABLE.get → KeyError / TypeError)
"""

import pytest

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

@pytest.fixture(autouse=True)
def _clear_table():
    """Each test starts with a clean _POLICY_TABLE."""
    original = dict(_POLICY_TABLE)
    _POLICY_TABLE.clear()
    yield
    _POLICY_TABLE.clear()
    _POLICY_TABLE.update(original)


# ------------------------------------------------------------------
# NotificationChannel enum 직접 테스트
# ------------------------------------------------------------------

class TestNotificationChannelEnum:
    """NotificationChannel enum이 세 개 상수를 가지는지 확인."""

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
        """user_overridable은 필수 필드 — 생성 시 ValidationError."""
        with pytest.raises(Exception):  # noqa: B017 pydantic ValidationError
            ChannelRule(channel=NotificationChannel.EMAIL)


class TestChannelPolicyModel:
    """ChannelPolicy 생성 검증."""

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
# ChannelPolicy.forced_channels 프로퍼티
# ------------------------------------------------------------------

class TestForcedChannels:
    """forced_channels: user_overridable=False인 채널만 반환."""

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
# get_channel_policy() 경계값 · negative
# ------------------------------------------------------------------

class TestGetChannelPolicyEdgeCases:
    """get_channel_policy() 잘못된 입력·경계값."""

    def test_known_event_returns_table_entry(self):
        result = get_channel_policy("approval.request.created")
        assert isinstance(result, ChannelPolicy)
        assert len(result.rules) == 1

    def test_unknown_event_returns_default(self):
        result = get_channel_policy("unknown.event.type")
        assert result == _DEFAULT_POLICY

    def test_empty_string_event_type(self):
        """빈 문자열은 테이블에 없으므로 _DEFAULT_POLICY."""
        result = get_channel_policy("")
        assert result == _DEFAULT_POLICY

    def test_none_event_type_returns_default(self):
        """None은 dict.get(None) → None → _DEFAULT_POLICY."""
        result = get_channel_policy(None)
        assert result == _DEFAULT_POLICY

    def test_special_characters_event_type(self):
        """특수문자 이벤트 타입은 테이블 미매칭 → _DEFAULT_POLICY."""
        result = get_channel_policy("event;DROP TABLE;--")
        assert result == _DEFAULT_POLICY

    def test_numeric_event_type(self):
        """숫자 이벤트 타입도 테이블 미매칭 → _DEFAULT_POLICY."""
        result = get_channel_policy("12345")
        assert result == _DEFAULT_POLICY

    def test_all_table_events_return_channel_policy(self):
        """테이블에 등록된 모든 이벤트가 ChannelPolicy 반환."""
        for event_type in _POLICY_TABLE:
            result = get_channel_policy(event_type)
            assert isinstance(result, ChannelPolicy)


# ------------------------------------------------------------------
# Failure injection (monkeypatch)
# ------------------------------------------------------------------

class TestFailureInjection:
    """의존성 예외 유발 테스트."""

    def test_policy_table_get_raises_key_error_propagates(self, monkeypatch):
        """_POLICY_TABLE.get() 가 KeyError를 raise하면 get_channel_policy가
        예외를 전파한다 (try/except 없음)."""
        # Replace the module-level _POLICY_TABLE entirely with a wrapper dict
        class FailingDict(dict):
            def get(self, key, default=None):
                raise KeyError(key)

        monkeypatch.setattr(
            "src.core.notifications.channel_policy._POLICY_TABLE",
            FailingDict(),
        )

        # try/except 없는 get_channel_policy는 KeyError를 전파
        with pytest.raises(KeyError):
            get_channel_policy("boom_event")

    def test_policy_table_raises_type_error(self, monkeypatch):
        """_POLICY_TABLE.get() 가 TypeError를 raise하면 예외가 전파된다."""
        class RaisingDict(dict):
            def get(self, key, default=None):
                raise TypeError("simulated storage failure")

        monkeypatch.setattr(
            "src.core.notifications.channel_policy._POLICY_TABLE",
            RaisingDict(),
        )

        with pytest.raises(TypeError):
            get_channel_policy("any_event")
