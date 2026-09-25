"""Connected Asset pure rule functions — must be unit-testable without DB/HTTP.

Spec: AIOSproject 74_connected_asset_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from src.foundation.connections.domain.models import CapabilityScope, ConnectionState

_ALLOWED_SCOPES = frozenset(s.value for s in CapabilityScope)

# Port the §2 state-transition table verbatim — do not allow transitions
# not in the table under the rationale that "there seems no way to call
# this path from two places simultaneously" (same principle as §2.2 of 105).
_ALLOWED_TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.PENDING_CONSENT: frozenset({ConnectionState.CONNECTING}),
    ConnectionState.CONNECTING: frozenset({ConnectionState.ACTIVE_READONLY}),
    ConnectionState.ACTIVE_READONLY: frozenset(
        {ConnectionState.DEGRADED, ConnectionState.REVOKED, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.DEGRADED: frozenset(
        {
            ConnectionState.ACTIVE_READONLY,
            ConnectionState.REVOKED,
            ConnectionState.DISCONNECTED,
        }
    ),
    ConnectionState.REVOKED: frozenset(),
    ConnectionState.DISCONNECTED: frozenset(),
}


class ForbiddenCapabilityScopeError(Exception):
    """74번 §1 "Any TRADE_*, WITHDRAW, TRANSFER, SIGN_*, unknown scope, or
    missing scope fingerprint is a hard rejection." — CON-002."""

    def __init__(self, rejected: list[str]) -> None:
        super().__init__(f"허용되지 않은 capability scope: {rejected}")
        self.rejected = rejected


class InvalidConnectionTransitionError(Exception):
    pass


def validate_capability_profile(requested: list[str]) -> tuple[CapabilityScope, ...]:
    """Validate a list of requested scope strings. Reject the entire list if
    any scope falls outside the P0 closed set (no partial acceptance —
    "hard rejection")."""
    if not requested:
        raise ForbiddenCapabilityScopeError(rejected=["<empty>"])
    rejected = [s for s in requested if s not in _ALLOWED_SCOPES]
    if rejected:
        raise ForbiddenCapabilityScopeError(rejected=rejected)
    return tuple(CapabilityScope(s) for s in requested)


def compute_scope_fingerprint(scopes: tuple[CapabilityScope, ...]) -> str:
    """Stable hash of the sorted scope list — used to detect scope drift by
    comparing CredentialBinding.scope_fingerprint with the
    ScopeProof.granted_scopes the provider actually granted
    (74 §5 "Alert on scope drift")."""
    payload = ",".join(sorted(s.value for s in scopes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_scope_drift(
    requested: tuple[CapabilityScope, ...], granted: tuple[CapabilityScope, ...]
) -> bool:
    return compute_scope_fingerprint(requested) != compute_scope_fingerprint(granted)


def is_transition_allowed(current: ConnectionState, target: ConnectionState) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def require_transition_allowed(current: ConnectionState, target: ConnectionState) -> None:
    if not is_transition_allowed(current, target):
        raise InvalidConnectionTransitionError(
            f"{current.value} -> {target.value} 전이는 허용되지 않습니다."
        )


class ProviderResponseClassification(str, Enum):
    """CON-006 "malformed/stale/duplicate provider response is classified
    and does not overwrite history" — among these three classifications,
    only FRESH is a storage target."""

    FRESH = "FRESH"
    STALE = "STALE"
    FUTURE_DATED = "FUTURE_DATED"


def classify_provider_response(
    *,
    provider_as_of: datetime,
    latest_known_as_of: datetime | None,
    now: datetime,
) -> ProviderResponseClassification:
    """Return FUTURE_DATED when the provider reports a future timestamp
    (clock skew / tampering) — reject storage entirely (same
    "INTEGRITY_FUTURE_DATA" principle shared by §76 documents). Return
    STALE when the timestamp is earlier than or equal to what is already
    known — do not overwrite "latest" with a late-arriving / retransmitted
    old response (prevents out-of-order responses from masquerading as
    latest by inspecting only `captured_at`). Otherwise return FRESH."""
    if provider_as_of > now:
        return ProviderResponseClassification.FUTURE_DATED
    if latest_known_as_of is not None and provider_as_of <= latest_known_as_of:
        return ProviderResponseClassification.STALE
    return ProviderResponseClassification.FRESH
