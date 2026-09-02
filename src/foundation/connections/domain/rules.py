"""Connected Asset 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 74_connected_asset_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

import hashlib

from src.foundation.connections.domain.models import CapabilityScope, ConnectionState

_ALLOWED_SCOPES = frozenset(s.value for s in CapabilityScope)

# 74번 §2 상태 전이표를 그대로 코드로 옮긴다 — "지금 이 경로를 두 곳에서
# 동시에 부를 방법이 없어 보인다"는 이유로 표에 없는 전이를 허용하지 않는다
# (105번 §2.2 원칙과 동일).
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
    """요청된 scope 문자열 목록을 검증한다. 하나라도 P0 closed set 밖이면
    전체를 거부한다(일부만 승인하는 부분 허용은 없음 — "hard rejection")."""
    if not requested:
        raise ForbiddenCapabilityScopeError(rejected=["<empty>"])
    rejected = [s for s in requested if s not in _ALLOWED_SCOPES]
    if rejected:
        raise ForbiddenCapabilityScopeError(rejected=rejected)
    return tuple(CapabilityScope(s) for s in requested)


def compute_scope_fingerprint(scopes: tuple[CapabilityScope, ...]) -> str:
    """정렬된 scope 목록의 안정적 해시 — CredentialBinding.scope_fingerprint와
    provider가 실제로 승인한 ScopeProof.granted_scopes를 비교해 scope drift를
    탐지하는 데 쓴다(74번 §5 "Alert on scope drift")."""
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
