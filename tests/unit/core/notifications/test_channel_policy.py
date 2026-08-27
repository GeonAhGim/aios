from src.core.notifications.channel_policy import NotificationChannel, get_channel_policy


def test_approval_request_forces_email_and_push():
    policy = get_channel_policy("approval.request.created")
    assert set(policy.forced_channels) == {NotificationChannel.EMAIL, NotificationChannel.PUSH}


def test_risk_profile_warning_mixes_forced_and_optional_channels():
    policy = get_channel_policy("risk_profile.match.warned")
    assert policy.forced_channels == [NotificationChannel.IN_APP]
    overridable = {r.channel for r in policy.rules if r.user_overridable}
    assert overridable == {NotificationChannel.EMAIL}


def test_marketplace_event_fully_overridable():
    policy = get_channel_policy("marketplace.purchase.requested")
    assert policy.forced_channels == []


def test_unknown_event_falls_back_to_safe_default():
    policy = get_channel_policy("some.brand.new.event")
    assert policy.forced_channels == []
    assert policy.rules[0].channel == NotificationChannel.EMAIL
    assert policy.rules[0].user_overridable is True
