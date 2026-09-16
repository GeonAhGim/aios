"""L4 §2.2(B) PLT-35 -- 비상 권한(break-glass) 요청/승인/소비.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2 (row
`src/core/security/break_glass.py`), §2.5 M7 DDL, §4 I12
("break-glass grant는 요청자≠승인자, ≤60분, 1회 소비").

DB가 1차 방어선이다(마이그레이션 `b4bb1b750621`의 두 CHECK 제약): 자기승인과
60분 초과 TTL은 애플리케이션 가드가 우회되거나 버그가 있어도 INSERT/UPDATE
자체가 거부된다. 이 모듈의 코드 가드(`approve_grant`의 사전 확인, `consume`의
조건부 UPDATE)는 사람이 읽을 수 있는 에러 메시지와 fail-closed 순서를 위한
것이지, 유일한 방어선이 아니다.

정직하게 남기는 한계(§10-10, 미확정): 스펙 §10-10은 "break-glass 2인 승인"이
운영자 1인 체제(ADR-2026-08-10)에서 승인자가 구조적으로 없다는 미해결 갭을
지적한다 -- `approve_grant`는 이 갭을 풀지 않는다(PM 결정 대기). 1인 체제에서는
`request_grant`로 만든 grant를 아무도 승인할 수 없다는 뜻이며, 이 모듈은 그
상태를 "승인 불가"로 정직하게 실패시킬 뿐 우회 경로를 만들지 않는다.
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
from src.services.auth.tokens import AuthLevel

BreakGlassScope = Literal["kill_switch_override", "tenant_read", "credential_revoke"]
BreakGlassState = Literal["REQUESTED", "APPROVED", "USED", "EXPIRED"]

MAX_GRANT_MINUTES = 60

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
    """request_grant/approve_grant 둘 다 auth_level=MFA_VERIFIED 필수(§2.2
    PLT-35). 403 AUTH_MFA_REQUIRED."""


class BreakGlassSelfApprovalError(Exception):
    """approver == requester -- 자기승인 금지(DB CHECK가 이중 방어선, I12).
    403 AUTHZ_FORBIDDEN."""


class BreakGlassInvalidStateError(Exception):
    """승인 대상이 REQUESTED가 아니거나, consume 대상이 APPROVED+미소비+미만료가
    아니거나(이미 소비/만료/미승인) -- 409 STATE_INVALID_TRANSITION."""


class AdminMfaRequiredError(Exception):
    """§2.2 PLT-35 -- break-glass 경로의 admin은 `auth_level == "MFA_VERIFIED"`가
    필수다(403 AUTH_MFA_REQUIRED). `src/api/admin_deps.py`의 `get_current_mfa_admin`이
    던진다 -- 여기(break_glass.py)에 두는 이유는 순환 임포트 회피다:
    `exception_registry.py`가 이 예외를 ErrorCode에 매핑하려면 import해야 하는데,
    `admin_deps.py`는 `src/api/deps.py`를 import하고 `deps.py`는
    `exception_mapping.py`(그 자매 모듈이 `exception_registry.py`)를 import해서
    admin_deps.py에 두면 순환이 생긴다. break_glass.py는 그 사슬 밖에 있다."""


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
    requester_auth_level: AuthLevel,
    scope: BreakGlassScope,
    reason: str,
    ttl_minutes: int = MAX_GRANT_MINUTES,
) -> BreakGlassGrant:
    """새 grant를 REQUESTED 상태로 만든다. `ttl_minutes`가 60을 넘으면 DB
    CHECK가 INSERT 자체를 거부한다(I12) -- 여기서는 그보다 먼저 사람이 읽을 수
    있는 메시지로 막는다."""
    if requester_auth_level != "MFA_VERIFIED":
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
    approver_auth_level: AuthLevel,
) -> BreakGlassGrant:
    """REQUESTED -> APPROVED. approver는 requester와 달라야 하고(코드 선확인 +
    DB CHECK 이중 방어) MFA_VERIFIED여야 한다."""
    if approver_auth_level != "MFA_VERIFIED":
        raise BreakGlassMfaRequiredError(
            f"approver_id={approver_id}: break-glass 승인은 MFA 재확인이 필요합니다."
        )

    existing = await conn.fetchrow(
        "SELECT requester_id FROM break_glass_grant WHERE id = $1", grant_id
    )
    if existing is None:
        raise BreakGlassInvalidStateError(f"grant_id={grant_id}: 존재하지 않습니다.")
    if existing["requester_id"] == approver_id:
        raise BreakGlassSelfApprovalError(
            f"grant_id={grant_id}: 요청자 본인은 승인할 수 없습니다(자기승인 금지)."
        )

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
    # 위 애플리케이션 검사를 TOCTOU로 우회해도(동시에 requester_id가 바뀌는
    # 일은 없지만) DB CHECK(approver_id <> requester_id)가 이 UPDATE 자체를
    # CheckViolationError로 거부한다 -- I12 이중 방어선.

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
    """APPROVED + 미소비 + 미만료 grant를 1회 소비한다(단일 조건부 UPDATE,
    105번 표준). `used_at IS NULL`과 `expires_at > now()`를 같은 WHERE에 걸어
    "이미 썼다"와 "만료됐다"를 별도 SELECT 없이 원자적으로 막는다 -- 두 실행이
    동시에 같은 grant를 consume하면 정확히 하나만 성공한다."""
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
        # 실패 사유(없음/미승인/이미 소비/만료)는 보안에 영향 없는 진단
        # 목적으로만 별도 조회한다 -- 이 조회 결과로 분기하지 않는다(위 UPDATE가
        # 이미 유일한 판정 지점).
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
