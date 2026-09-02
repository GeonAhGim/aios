"""FND-05 Connected Asset 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.connections.domain.models import CapabilityScope, ConnectionState
from src.foundation.connections.domain.rules import (
    ForbiddenCapabilityScopeError,
    InvalidConnectionTransitionError,
    ProviderResponseClassification,
    classify_provider_response,
    compute_scope_fingerprint,
    detect_scope_drift,
    is_transition_allowed,
    require_transition_allowed,
    validate_capability_profile,
)

_NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_validate_capability_profile_accepts_p0_scopes():
    scopes = validate_capability_profile(["READ_BALANCE", "READ_POSITION"])
    assert scopes == (CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)


@pytest.mark.parametrize(
    "requested",
    [
        ["TRADE_SPOT"],
        ["WITHDRAW"],
        ["TRANSFER"],
        ["SIGN_TRANSACTION"],
        ["UNKNOWN_SCOPE"],
        ["READ_BALANCE", "TRADE_SPOT"],
    ],
)
def test_validate_capability_profile_rejects_forbidden_scopes(requested):
    """CON-002 — trade/withdraw/unknown scope is rejected before vault binding."""
    with pytest.raises(ForbiddenCapabilityScopeError):
        validate_capability_profile(requested)


def test_validate_capability_profile_rejects_empty():
    with pytest.raises(ForbiddenCapabilityScopeError):
        validate_capability_profile([])


def test_scope_fingerprint_is_order_independent():
    a = compute_scope_fingerprint((CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION))
    b = compute_scope_fingerprint((CapabilityScope.READ_POSITION, CapabilityScope.READ_BALANCE))
    assert a == b


def test_detect_scope_drift_true_when_granted_differs():
    requested = (CapabilityScope.READ_BALANCE,)
    granted = (CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
    assert detect_scope_drift(requested, granted) is True


def test_detect_scope_drift_false_when_identical():
    scopes = (CapabilityScope.READ_BALANCE, CapabilityScope.READ_ACTIVITY)
    assert detect_scope_drift(scopes, scopes) is False


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (ConnectionState.PENDING_CONSENT, ConnectionState.CONNECTING, True),
        (ConnectionState.CONNECTING, ConnectionState.ACTIVE_READONLY, True),
        (ConnectionState.ACTIVE_READONLY, ConnectionState.DEGRADED, True),
        (ConnectionState.ACTIVE_READONLY, ConnectionState.REVOKED, True),
        (ConnectionState.DEGRADED, ConnectionState.ACTIVE_READONLY, True),
        (ConnectionState.DEGRADED, ConnectionState.REVOKED, True),
        # 74번 §2 표에 없는 전이는 전부 거부한다.
        (ConnectionState.PENDING_CONSENT, ConnectionState.ACTIVE_READONLY, False),
        (ConnectionState.REVOKED, ConnectionState.ACTIVE_READONLY, False),
        (ConnectionState.REVOKED, ConnectionState.CONNECTING, False),
        (ConnectionState.DISCONNECTED, ConnectionState.ACTIVE_READONLY, False),
    ],
)
def test_is_transition_allowed_matches_state_table(current, target, allowed):
    assert is_transition_allowed(current, target) is allowed


def test_require_transition_allowed_raises_on_invalid_transition():
    with pytest.raises(InvalidConnectionTransitionError):
        require_transition_allowed(ConnectionState.REVOKED, ConnectionState.ACTIVE_READONLY)


def test_classify_first_ever_response_is_fresh():
    """CON-006 — 이전 스냅샷이 없으면(첫 sync) 비교 대상이 없으니 FRESH."""
    result = classify_provider_response(
        provider_as_of=_NOW, latest_known_as_of=None, now=_NOW
    )
    assert result == ProviderResponseClassification.FRESH


def test_classify_newer_than_latest_is_fresh():
    result = classify_provider_response(
        provider_as_of=_NOW, latest_known_as_of=_NOW - timedelta(hours=1), now=_NOW
    )
    assert result == ProviderResponseClassification.FRESH


def test_classify_older_than_latest_is_stale():
    """CON-006 — 지연 도착한 오래된 응답이 이미 아는 것보다 과거면 STALE."""
    result = classify_provider_response(
        provider_as_of=_NOW - timedelta(hours=1), latest_known_as_of=_NOW, now=_NOW
    )
    assert result == ProviderResponseClassification.STALE


def test_classify_exact_duplicate_timestamp_is_stale():
    result = classify_provider_response(
        provider_as_of=_NOW, latest_known_as_of=_NOW, now=_NOW
    )
    assert result == ProviderResponseClassification.STALE


def test_classify_future_timestamp_is_future_dated():
    """CON-006 — provider가 미래 시각을 보고하면(시계 오류·변조 가능성)
    FUTURE_DATED, latest_known_as_of가 없어도 거부한다."""
    result = classify_provider_response(
        provider_as_of=_NOW + timedelta(seconds=1), latest_known_as_of=None, now=_NOW
    )
    assert result == ProviderResponseClassification.FUTURE_DATED
