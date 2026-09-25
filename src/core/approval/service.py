"""10.1 — Critical Risk approval request creation/processing (ApprovalService).

Spec: 기능설계문서_v1.20.md#FD-10.1, ADR-2026-08-10-D

- scope="USER": user-level as allowed by policy doc 4.9 — SOLO (self alone) / DUAL
  (sequential signatures from two different accounts), mandatory_wait_seconds floor
  of 60s (§13.1, same floor as FD-11.3 ApprovalMode).
- scope="PLATFORM": platform-wide Kill Switch / Circuit Breaker re-activation etc. —
  floor of 180s per ADR-2026-08-10-D §③, single-signer regime (conditional).

Deviation/interpretation: the FD-10.1 text describes both the "60s timer (button
disabled)" and "expires_at (60s later — rejected if no response)" as the same 60
seconds, but a literal implementation would make "wait-end time and auto-reject
time identical," collapsing the approval window to effectively 0 seconds — unusable
for both real use and tests. Here mandatory_wait_seconds (when the approve button
becomes enabled) is separated from the subsequent response window
(RESPONSE_WINDOW_SECONDS, Draft 5 min), so expires_at = created_at +
mandatory_wait_seconds + RESPONSE_WINDOW_SECONDS.

Deviation (2026-09-01, gap found after app assembly): approve()/reject() themselves
do not verify "who called" — even though scope="USER" (SOLO = self alone, DUAL =
sequential signatures from two different accounts), the only HTTP exposure was
admin.py's admin-only endpoints. Added a self-service endpoint in
src/api/routers/users.py that only handles the caller's own requests
(/users/me/approval-requests/*), opening up approve/reject restricted to
"requests owned by the caller" — this is enough for all of SOLO and DUAL's first
signature, but DUAL's second signer has no registered identity in the system
(user_approval_settings.second_approver_contact is just a contact string, with no
logic resolving it to a user_id) — since anyone could claim to be the "second
signer" without verification, that path is still left admin-only (an honest
reduction in scope until identity resolution is designed).

task-1723 P1-D: this module was originally 303 lines (over the P6 300-line cap)
and was split via a pure move — the ApprovalRequest model / shared lookup helpers
(ApprovalError, _row_to_model, _fetch) moved to _shared.py. The approve/reject
logic stays here because tests monkeypatch `_fetch` as a module attribute
(tests/integration/test_approval_service.py reproduces a concurrency race) — since
that monkeypatch targets this module's global `_fetch` binding, approve/reject
etc. remain in this module where the monkeypatch is visible.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg

from src.core.approval._shared import ApprovalError, ApprovalRequest, _fetch, _row_to_model
from src.core.security.segregation_of_duty_port import (
    SegregationOfDutyViolation,
    assert_actor_not_counterparty,
)
from src.data.models.serialization import DecimalSafeEncoder

__all__ = [
    "ApprovalError",
    "ApprovalRequest",
    "approve",
    "cancel",
    "create_request",
    "expire_pending",
    "get_request",
    "list_pending",
    "reject",
]

USER_WAIT_SECONDS = 60
PLATFORM_WAIT_SECONDS = 180
RESPONSE_WINDOW_SECONDS = 300  # Draft — see the deviation note in the docstring above

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


async def get_request(pool: asyncpg.Pool, request_id: int) -> ApprovalRequest:
    """Used when another service (e.g. 9.4b Circuit Breaker re-activation) polls request status."""
    return await _fetch(pool, request_id)


async def list_pending(
    pool: asyncpg.Pool,
    *,
    scope: str | None = None,
    user_id: UUID | None = None,
) -> list[ApprovalRequest]:
    """Deviation (2026-09-01, gap found after app assembly): the spec nowhere
    describes a way to list pending approval requests, so a requester had no way
    to learn their own request id (FD-17's dispatcher doesn't exist yet, so there's
    no notification-body deep link either) — get_request() can only be used once
    the id is already known. Filtering by user_id (scope="USER" requests always
    have user_id populated) naturally shows only the caller's own requests."""
    conditions = ["status = 'PENDING'"]
    params: list[object] = []
    if scope is not None:
        params.append(scope)
        conditions.append(f"scope = ${len(params)}")
    if user_id is not None:
        params.append(user_id)
        conditions.append(f"user_id = ${len(params)}")
    where_clause = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM approval_requests WHERE {where_clause} ORDER BY created_at DESC",
            *params,
        )
    return [_row_to_model(row) for row in rows]


async def create_request(
    pool: asyncpg.Pool,
    *,
    scope: str,
    trigger_source: str,
    requested_action: str,
    context: dict[str, Any],
    approval_mode: str = "SOLO",
    user_id: UUID | None = None,
    provenance: str | None = None,
    publish: PublishFn | None = None,
) -> ApprovalRequest:
    if scope == "PLATFORM":
        wait_seconds = PLATFORM_WAIT_SECONDS
    elif scope == "USER":
        wait_seconds = USER_WAIT_SECONDS
    else:
        raise ApprovalError(f"알 수 없는 scope: {scope}")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=wait_seconds + RESPONSE_WINDOW_SECONDS)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO approval_requests
                (scope, user_id, trigger_source, provenance, context, requested_action,
                 approval_mode, mandatory_wait_seconds, expires_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            RETURNING *
            """,
            scope,
            user_id,
            trigger_source,
            provenance,
            json.dumps(context, cls=DecimalSafeEncoder),
            requested_action,
            approval_mode,
            wait_seconds,
            expires_at,
        )
    result = _row_to_model(row)

    # PLATFORM scope (no specific user_id) has no "broadcast to all admins" design
    # yet, so it is not published today — publishing without a recipient would make
    # NotificationGateway fail every time on a missing user_id (a judgment call that
    # a quiet omission beats a fake success; once that gate exists, just lift this
    # condition).
    if publish is not None and scope == "USER" and user_id is not None:
        await publish(
            "approval.request.created",
            {
                "event_type": "approval.request.created",
                "user_id": str(user_id),
                "approval_request_id": result.id,
                "requested_action": requested_action,
            },
        )

    return result


async def approve(pool: asyncpg.Pool, request_id: int, approver_id: UUID) -> ApprovalRequest:
    """Reflects red-team audit findings (docs/RED_TEAM_FINDINGS.md #04) — "read then
    write separately" can let two near-simultaneous approvals both pass (SOLO
    double-approval, DUAL first-signer forgery). Each of the three UPDATEs below
    re-checks the actual DB state at that moment in its WHERE clause to make it
    atomic — if RETURNING comes back empty, it means another request already
    changed the status in the meantime, so it fails with ApprovalError (the same
    pattern used by wallet_service.py::confirm_topup())."""
    request = await _fetch(pool, request_id)
    if request.status != "PENDING":
        raise ApprovalError(f"이미 처리된 요청: status={request.status}")

    now = datetime.now(timezone.utc)
    if now < request.created_at + timedelta(seconds=request.mandatory_wait_seconds):
        raise ApprovalError("강제 대기시간이 아직 지나지 않았습니다.")
    if now > request.expires_at:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE approval_requests SET status = 'EXPIRED', resolved_at = $2 "
                "WHERE id = $1 AND status = 'PENDING'",
                request_id,
                now,
            )
        raise ApprovalError("요청이 만료되어 자동 거부되었습니다.")

    if request.approval_mode == "SOLO":
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE approval_requests SET status = 'APPROVED', first_approver_id = $2, "
                "resolved_at = $3 WHERE id = $1 AND status = 'PENDING' RETURNING *",
                request_id,
                approver_id,
                now,
            )
        if row is None:
            raise ApprovalError("이미 처리된 요청입니다(동시 요청 충돌).")
        return _row_to_model(row)

    # DUAL — sequential signatures from different accounts (principle 4.9)
    if request.first_approver_id is None:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE approval_requests SET first_approver_id = $2 "
                "WHERE id = $1 AND status = 'PENDING' AND first_approver_id IS NULL "
                "RETURNING *",
                request_id,
                approver_id,
            )
        if row is None:
            raise ApprovalError("이미 다른 사용자가 먼저 서명했습니다(동시 요청 충돌).")
        return _row_to_model(row)

    try:
        assert_actor_not_counterparty(
            approver_id,
            request.first_approver_id,
            action="approval_request.dual_second_sign",
        )
    except SegregationOfDutyViolation as exc:
        raise ApprovalError("DUAL 모드는 서로 다른 계정의 순차 서명이 필요합니다.") from exc

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE approval_requests SET status = 'APPROVED', second_approver_id = $2, "
            "resolved_at = $3 WHERE id = $1 AND status = 'PENDING' "
            "AND first_approver_id IS NOT NULL AND first_approver_id != $2 RETURNING *",
            request_id,
            approver_id,
            now,
        )
    if row is None:
        raise ApprovalError("이미 처리됐거나 동시 서명 충돌이 발생했습니다.")
    return _row_to_model(row)


async def reject(pool: asyncpg.Pool, request_id: int, approver_id: UUID) -> ApprovalRequest:
    request = await _fetch(pool, request_id)
    if request.status != "PENDING":
        raise ApprovalError(f"이미 처리된 요청: status={request.status}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE approval_requests SET status = 'REJECTED', resolved_at = $2 "
            "WHERE id = $1 AND status = 'PENDING' RETURNING *",
            request_id,
            datetime.now(timezone.utc),
        )
    if row is None:
        raise ApprovalError("이미 처리된 요청입니다(동시 요청 충돌).")
    return _row_to_model(row)


async def cancel(pool: asyncpg.Pool, request_id: int) -> ApprovalRequest:
    """9.4b/9.6 — auto-cancel on condition re-deterioration while pending (blocks
    the path of re-activating from a deteriorated state at the source)."""
    return await _update(
        pool, request_id, status="CANCELLED", resolved_at=datetime.now(timezone.utc)
    )


async def expire_pending(pool: asyncpg.Pool) -> list[int]:
    """FD-10.1 exception case — auto-reject if nobody responds before the timer
    expires (fail-safe, no implicit approval). Called periodically (e.g. from the
    safety loop)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE approval_requests
                SET status = 'EXPIRED', resolved_at = now()
                WHERE status = 'PENDING' AND expires_at < now()
                RETURNING id
            """
        )
    return [row["id"] for row in rows]


async def _update(pool: asyncpg.Pool, request_id: int, **fields: Any) -> ApprovalRequest:
    columns = list(fields.keys())
    values = [fields[c] for c in columns]
    set_clause = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE approval_requests SET {set_clause} WHERE id = $1 RETURNING *",
            request_id,
            *values,
        )
    return _row_to_model(row)
