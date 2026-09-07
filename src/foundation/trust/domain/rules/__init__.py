"""Trust Core pure rule functions — must be unit-testable without DB/HTTP.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §6.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from src.foundation.trust.domain.models import (
    Consent,
    ConsentState,
    Disclosure,
    MembershipRole,
    MembershipState,
)


def is_disclosure_acceptable(disclosure: Disclosure, *, now: datetime) -> bool:
    """A retired disclosure cannot accept new consent (spec 73 §4
    VALIDATION_DISCLOSURE_RETIRED)."""
    return disclosure.retired_at is None or disclosure.retired_at > now


def is_consent_fresh(
    consent: Consent | None,
    *,
    required_disclosure: Disclosure,
    now: datetime,
) -> bool:
    """Spec 73 §6 rule 3 — a required consent is fresh only if it satisfies
    both the purpose and the active disclosure revision. A prior revision
    is not sufficient (if a re-consent-required policy applies). An expired
    consent is immediately void even if background expiry processing lags
    (spec 73 §3.2)."""
    if consent is None:
        return False
    if consent.state != ConsentState.ACTIVE:
        return False
    if consent.purpose != required_disclosure.purpose:
        return False
    if consent.disclosure_revision != required_disclosure.revision:
        return False
    if consent.expires_at is not None and now >= consent.expires_at:
        return False
    return True


def freshness_denial_reason(
    consent: Consent | None,
    *,
    required_disclosure: Disclosure,
    now: datetime,
) -> str | None:
    """Returns a reason_code matching the spec 72 §4 error taxonomy when
    is_consent_fresh() is False. None if fresh."""
    if consent is None:
        return "POLICY_CONSENT_REQUIRED"
    if consent.state == ConsentState.REVOKED:
        return "POLICY_CONSENT_REVOKED"
    if consent.disclosure_revision != required_disclosure.revision:
        return "POLICY_CONSENT_STALE_REVISION"
    if consent.expires_at is not None and now >= consent.expires_at:
        return "POLICY_CONSENT_EXPIRED"
    return None


_TransitionKey = tuple[MembershipState, MembershipState]
_MEMBERSHIP_TRANSITIONS: dict[_TransitionKey, frozenset[MembershipRole]] = {
    # Spec 73 §3.1 state machine. "admin/risk"'s risk (automated risk-
    # management trigger) is not a human, so it maps to the SERVICE role.
    # The MFA requirement for REVOKED->ACTIVE is verified outside this pure
    # function (by the caller) — here only role is determined.
    (MembershipState.ACTIVE, MembershipState.SUSPENDED): frozenset(
        {MembershipRole.ADMIN, MembershipRole.SERVICE}
    ),
    (MembershipState.ACTIVE, MembershipState.REVOKED): frozenset(
        {MembershipRole.OWNER, MembershipRole.ADMIN}
    ),
    (MembershipState.SUSPENDED, MembershipState.REVOKED): frozenset(
        {MembershipRole.OWNER, MembershipRole.ADMIN}
    ),
    (MembershipState.REVOKED, MembershipState.ACTIVE): frozenset({MembershipRole.OWNER}),
}


def is_membership_transition_allowed(
    from_: MembershipState, to: MembershipState, *, actor_role: MembershipRole
) -> bool:
    """Spec 73 §3.1 transition table. Any transition not in the table
    (including a transition to the same state) is denied."""
    allowed_actors = _MEMBERSHIP_TRANSITIONS.get((from_, to))
    if allowed_actors is None:
        return False
    return actor_role in allowed_actors


def would_remove_last_owner(active_owners: int, target_is_owner: bool, to: MembershipState) -> bool:
    """Spec 73 §3.1 "cannot remove last owner" guard. `active_owners` is the
    count of active OWNERs before the transition, including the target
    membership (the result of `SELECT ... FOR UPDATE` in the same
    transaction)."""
    if not target_is_owner:
        return False
    if to == MembershipState.ACTIVE:
        return False
    return active_owners <= 1


def role_can(role: MembershipRole, action: Literal["read", "mutate", "admin"]) -> bool:
    """A role's default permissions within a tenant. AUDITOR is read-only as
    the name implies — granting write permission to an audit-purpose role
    would render the spec 73 §8 "tenant-confidential" boundary meaningless."""
    if action == "read":
        return True
    if action == "mutate":
        return role in {
            MembershipRole.OWNER,
            MembershipRole.ADMIN,
            MembershipRole.MEMBER,
            MembershipRole.SERVICE,
        }
    if action == "admin":
        return role in {MembershipRole.OWNER, MembershipRole.ADMIN}
    return False
