"""FND-05 Connected Asset 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

import pytest

from src.foundation.connections.domain.models import CapabilityScope, ConnectionState
from src.foundation.connections.domain.rules import (
    ForbiddenCapabilityScopeError,
    InvalidConnectionTransitionError,
    compute_scope_fingerprint,
    detect_scope_drift,
    is_transition_allowed,
    require_transition_allowed,
    validate_capability_profile,
)


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
