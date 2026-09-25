"""L4 Sec 2.2(B) PLT-35 -- break-glass grant request/approve/consume.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md Sec 2.2 (row
`src/core/security/break_glass.py`), Sec 2.5 M7 DDL, Sec 4 I12
("a break-glass grant requires requester != approver, TTL <= 60min, single use").

The DB is the first line of defense (migration `b4bb1b750621`'s two CHECK
constraints): self-approval and a >60min TTL are rejected at the INSERT/UPDATE
itself even if the application guard is bypassed or buggy. This module's code
guards (the pre-check in `approve_grant`, the conditional UPDATE in `consume`)
exist for readable error messages and fail-closed ordering, not as the sole
line of defense.

Known gap, stated honestly (spec Sec 10-10, undecided): the spec's Sec 10-10
flags that "break-glass two-person approval" has no structural approver in a
single-operator regime (ADR-2026-08-10) -- `approve_grant` does not solve that
gap (pending a PM decision). Under a single-operator regime, a grant created
by `request_grant` cannot be approved by anyone; this module fails that state
honestly as "cannot approve" rather than building a workaround.

task-3795 (REJECT) / task-6482 fix: `request_grant`/`approve_grant` used to
accept a static `auth_level` claim baked into the JWT at login and carried
forward unchanged across refresh for up to `REFRESH_TTL_DAYS` (14 days,
`src/services/auth/tokens.py`). Both functions (and `get_current_mfa_admin`
in `src/api/admin_deps.py`, gating all three break-glass phases) now take
the raw `mfa_verified_at` timestamp (re-read from the DB every request by
`get_current_user`, so never stale) and require it within
`MFA_STEP_UP_WINDOW`, the same rule `foundation_deps.py::_compute_mfa_verified`
already applies to foundation routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.core.logging.audit_log import record_audit_log
from src.core.observability.metric_names import SECURITY_BREAK_GLASS_COUNT_TOTAL
from src.core.observability.metrics import metrics
from src.core.security.segregation_of_duty_port import (
    SegregationOfDutyChecker,
    SegregationOfDutyViolation,
)

BreakGlassScope = Literal["kill_switch_override", "tenant_read", "credential_revoke"]
BreakGlassState = Literal["REQUESTED", "APPROVED", "USED", "EXPIRED"]

MAX_GRANT_MINUTES = 60

# task-3795 finding: a JWT `auth_level` claim is fixed at login/refresh time and
# stays "MFA_VERIFIED" for the session's whole life (up to REFRESH_TTL_DAYS) even
# if TOTP was passed once weeks ago. `mfa_verified_at` is a DB timestamp re-read
# on every request (`get_current_user`), so freshness against this window is the
# actual step-up guarantee — same value as `src/api/foundation_deps.py`'s
# MFA_STEP_UP_WINDOW (kept as a separate constant: core/ cannot import api/).
MFA_STEP_UP_WINDOW = timedelta(minutes=15)


def mfa_step_up_fresh(mfa_verified_at: datetime | None, *, now: datetime | None = None) -> bool:
    """True only if TOTP was verified within the last `MFA_STEP_UP_WINDOW` —
    `mfa_verified_at is None` (MFA never verified this session) is always False."""
    if mfa_verified_at is None:
        return False
    now = now if now is not None else datetime.now(timezone.utc)
    return (now - mfa_verified_at) <= MFA_STEP_UP_WINDOW

_RETURNING_COLUMNS = (
    "id, requester_id, approver_id, scope, reason, state, created_at, expires_at, used_at"
)


class BreakGlassGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    requester_id: UUID
    approver_id: UUID | None
    scope: BreakGlassScope
    reason: str
    state: BreakGlassState
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None


class BreakGlassMfaRequiredError(Exception):
    """Both request_grant/approve_grant require auth_level=MFA_VERIFIED
    (Sec 2.2 PLT-35). 403 AUTH_MFA_REQUIRED."""


class BreakGlassSelfApprovalError(Exception):
    """approver == requester -- self-approval is forbidden (DB CHECK is the
    second line of defense, I12). 403 AUTHZ_FORBIDDEN."""


class BreakGlassInvalidStateError(Exception):
    """The approval target isn't REQUESTED, or the consume target isn't
    APPROVED+unused+unexpired (already used/expired/not yet approved) --
    409 STATE_INVALID_TRANSITION."""


class AdminMfaRequiredError(Exception):
    """Sec 2.2 PLT-35 -- an admin on the break-glass path requires a fresh
    MFA step-up (403 AUTH_MFA_REQUIRED). Raised by `get_current_mfa_admin`
    in `src/api/admin_deps.py`; it lives here to avoid a circular import
    (admin_deps.py -> deps.py -> exception_mapping.py -> exception_registry.py,
    which needs this class -- break_glass.py sits outside that chain)."""


def _row_to_grant(row: asyncpg.Record) -> BreakGlassGrant:
    return BreakGlassGrant(
        id=row["id"],
        requester_id=row["requester_id"],
        approver_id=row["approver_id"],
        scope=row["scope"],
        reason=row["reason"],
        state=row["state"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
    )


async def request_grant(
    conn: asyncpg.Connection,
    *,
    requester_id: UUID,
    requester_mfa_verified_at: datetime | None,
    scope: BreakGlassScope,
    reason: str,
    ttl_minutes: int = MAX_GRANT_MINUTES,
) -> BreakGlassGrant:
    """Creates a new grant in the REQUESTED state. If `ttl_minutes` exceeds
    60, the DB CHECK rejects the INSERT itself (I12) -- this raises a
    human-readable error before that, first.

    `requester_mfa_verified_at` must be within `MFA_STEP_UP_WINDOW` of now --
    a stale TOTP pass (session left open past the window, task-3795) is
    treated the same as "never verified"."""
    if not mfa_step_up_fresh(requester_mfa_verified_at):
        raise BreakGlassMfaRequiredError(
            f"requester_id={requester_id}: break-glass 요청은 MFA 재확인이 필요합니다."
        )
    if ttl_minutes <= 0 or ttl_minutes > MAX_GRANT_MINUTES:
        raise ValueError(f"ttl_minutes={ttl_minutes}: 0 초과 {MAX_GRANT_MINUTES} 이하여야 합니다.")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ttl_minutes)
    row = await conn.fetchrow(
        f"""
        INSERT INTO break_glass_grant (requester_id, scope, reason, expires_at)
        VALUES ($1, $2, $3, $4)
        RETURNING {_RETURNING_COLUMNS}
        """,  # noqa: S608 -- _RETURNING_COLUMNS is a module constant, no user input
        requester_id,
        scope,
        reason,
        expires_at,
    )
    grant = _row_to_grant(row)
    await record_audit_log(
        conn,
        actor_agent=str(requester_id),
        action_type="security.break_glass_requested",
        user_id=requester_id,
        target_type="break_glass_grant",
        target_id=str(grant.id),
        decision_data={"scope": scope, "expires_at": expires_at.isoformat()},
    )
    metrics().counter(SECURITY_BREAK_GLASS_COUNT_TOTAL, {"scope": scope, "phase": "requested"})
    return grant


async def approve_grant(
    conn: asyncpg.Connection,
    *,
    grant_id: UUID,
    approver_id: UUID,
    approver_mfa_verified_at: datetime | None,
    check_segregation_of_duty: SegregationOfDutyChecker,
) -> BreakGlassGrant:
    """REQUESTED -> APPROVED. The approver must differ from the requester
    (code pre-check + DB CHECK, defense in depth) and must have a fresh
    MFA step-up (`approver_mfa_verified_at` within `MFA_STEP_UP_WINDOW`,
    task-3795 -- not just a static "MFA_VERIFIED" claim from login).

    `check_segregation_of_duty` is injected (not imported directly) because
    `src/core` cannot import `src/foundation` (RATCHET-2 core-no-io) — the
    caller (`src/api/routers/admin_break_glass.py`, the composition root)
    passes `foundation.trust.domain.rules.segregation_of_duty.
    assert_actor_not_counterparty`, the PLT-43 single definition of this
    invariant.
    """
    if not mfa_step_up_fresh(approver_mfa_verified_at):
        raise BreakGlassMfaRequiredError(
            f"approver_id={approver_id}: break-glass 승인은 MFA 재확인이 필요합니다."
        )

    existing = await conn.fetchrow(
        "SELECT requester_id FROM break_glass_grant WHERE id = $1", grant_id
    )
    if existing is None:
        raise BreakGlassInvalidStateError(f"grant_id={grant_id}: 존재하지 않습니다.")
    try:
        check_segregation_of_duty(
            approver_id, existing["requester_id"], action="break_glass.approve_grant"
        )
    except SegregationOfDutyViolation as exc:
        raise BreakGlassSelfApprovalError(
            f"grant_id={grant_id}: 요청자 본인은 승인할 수 없습니다(자기승인 금지)."
        ) from exc

    try:
        row = await conditional_update(
            conn,
            table="break_glass_grant",
            id_column="id",
            id_value=grant_id,
            expected_state_column="state",
            expected_state_value="REQUESTED",
            set_values={"approver_id": approver_id, "state": "APPROVED"},
            returning=_RETURNING_COLUMNS,
        )
    except ConcurrencyConflictError as exc:
        raise BreakGlassInvalidStateError(
            f"grant_id={grant_id}: REQUESTED 상태가 아니어서 승인할 수 없습니다."
        ) from exc
    # Even if the application check above is bypassed via a TOCTOU race (the
    # requester_id itself can't change concurrently, but as defense in depth),
    # the DB CHECK (approver_id <> requester_id) rejects this same UPDATE with
    # a CheckViolationError -- I12's second line of defense.

    grant = _row_to_grant(row)
    await record_audit_log(
        conn,
        actor_agent=str(approver_id),
        action_type="security.break_glass_approved",
        user_id=approver_id,
        target_type="break_glass_grant",
        target_id=str(grant.id),
        decision_data={"scope": grant.scope, "requester_id": str(grant.requester_id)},
    )
    metrics().counter(SECURITY_BREAK_GLASS_COUNT_TOTAL, {"scope": grant.scope, "phase": "approved"})
    return grant


async def consume(conn: asyncpg.Connection, *, grant_id: UUID, admin_id: UUID) -> BreakGlassGrant:
    """Consumes an APPROVED + unused + unexpired grant exactly once (a single
    conditional UPDATE, per the 105 standard). `used_at IS NULL` and
    `expires_at > now()` are checked in the same WHERE clause, atomically
    ruling out "already used" and "expired" without a separate SELECT -- if
    two callers race to consume the same grant, exactly one succeeds."""
    now = datetime.now(timezone.utc)
    row = await conn.fetchrow(
        f"""
        UPDATE break_glass_grant
        SET state = 'USED', used_at = $2
        WHERE id = $1 AND state = 'APPROVED' AND used_at IS NULL AND expires_at > $2
        RETURNING {_RETURNING_COLUMNS}
        """,  # noqa: S608 -- _RETURNING_COLUMNS is a module constant, no user input
        grant_id,
        now,
    )
    if row is None:
        # The failure reason (missing/not-approved/already-used/expired) is
        # looked up separately for diagnostics only, with no security
        # implication -- this lookup does not branch behavior (the UPDATE
        # above is already the sole decision point).
        reason_row = await conn.fetchrow(
            "SELECT state, used_at, expires_at FROM break_glass_grant WHERE id = $1", grant_id
        )
        if reason_row is None:
            reason = "존재하지 않습니다"
        elif reason_row["used_at"] is not None:
            reason = "이미 소비되었습니다"
        elif reason_row["expires_at"] <= now:
            reason = "만료되었습니다"
        else:
            reason = f"state={reason_row['state']}(APPROVED 아님)"
        raise BreakGlassInvalidStateError(f"grant_id={grant_id}: {reason}")

    grant = _row_to_grant(row)
    await record_audit_log(
        conn,
        actor_agent=str(admin_id),
        action_type="security.break_glass_used",
        user_id=admin_id,
        target_type="break_glass_grant",
        target_id=str(grant.id),
        decision_data={"scope": grant.scope, "requester_id": str(grant.requester_id)},
    )
    metrics().counter(SECURITY_BREAK_GLASS_COUNT_TOTAL, {"scope": grant.scope, "phase": "used"})
    return grant
