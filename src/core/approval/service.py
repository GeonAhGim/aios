"""10.1 — Critical Risk 승인 요청 생성/처리 (ApprovalService).

Spec: 기능설계문서_v1.20.md#FD-10.1, ADR-2026-08-10-D

- scope="USER": 정책문서 4.9가 허용한 사용자 레벨 — SOLO(본인 1인)/DUAL
  (서로 다른 계정의 순차 서명), mandatory_wait_seconds 하한 60초(13번
  §13.1, FD-11.3 ApprovalMode와 동일 하한).
- scope="PLATFORM": 시스템 전역 Kill Switch·Circuit Breaker 재가동 등 —
  ADR-2026-08-10-D §③ 확정대로 하한 180초, 1인 체제(조건부).

편차/해석: FD-10.1 원문은 "60초 타이머(버튼 비활성화)"와 "expires_at(60초
후 — 응답 없으면 거부)"를 같은 60초로 서술하는데, 문자 그대로 구현하면
"대기 종료 시점과 자동거부 시점이 동일"해져 승인 가능 창이 사실상 0초가
된다 — 실사용/테스트 모두 불가능. 여기서는 mandatory_wait_seconds(승인
버튼이 열리는 시점)와 이후 응답 가능 창(RESPONSE_WINDOW_SECONDS, Draft
5분)을 분리해 expires_at = created_at + mandatory_wait_seconds +
RESPONSE_WINDOW_SECONDS로 계산한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.data.models.serialization import DecimalSafeEncoder

USER_WAIT_SECONDS = 60
PLATFORM_WAIT_SECONDS = 180
RESPONSE_WINDOW_SECONDS = 300  # Draft — 위 docstring 편차 설명 참조


class ApprovalError(Exception):
    """이 모듈이 던지는 비즈니스 규칙 위반 — 호출부가 사용자에게 사유를 보여줄 수 있다."""


class ApprovalRequest(BaseModel):
    id: int
    scope: str
    user_id: UUID | None
    trigger_source: str
    provenance: str | None
    context: dict[str, Any]
    requested_action: str
    approval_mode: str
    status: str
    mandatory_wait_seconds: int
    first_approver_id: UUID | None
    second_approver_id: UUID | None
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


def _row_to_model(row: asyncpg.Record) -> ApprovalRequest:
    data = dict(row)
    data["context"] = json.loads(data["context"])
    return ApprovalRequest(**data)


async def _fetch(pool: asyncpg.Pool, request_id: int) -> ApprovalRequest:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM approval_requests WHERE id = $1", request_id
        )
    if row is None:
        raise ApprovalError(f"승인 요청을 찾을 수 없음: id={request_id}")
    return _row_to_model(row)


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
    return _row_to_model(row)


async def approve(pool: asyncpg.Pool, request_id: int, approver_id: UUID) -> ApprovalRequest:
    request = await _fetch(pool, request_id)
    if request.status != "PENDING":
        raise ApprovalError(f"이미 처리된 요청: status={request.status}")

    now = datetime.now(timezone.utc)
    if now < request.created_at + timedelta(seconds=request.mandatory_wait_seconds):
        raise ApprovalError("강제 대기시간이 아직 지나지 않았습니다.")
    if now > request.expires_at:
        await _update(pool, request_id, status="EXPIRED", resolved_at=now)
        raise ApprovalError("요청이 만료되어 자동 거부되었습니다.")

    if request.approval_mode == "SOLO":
        return await _update(
            pool,
            request_id,
            status="APPROVED",
            first_approver_id=approver_id,
            resolved_at=now,
        )

    # DUAL — 서로 다른 계정의 순차 서명(4.9 원칙)
    if request.first_approver_id is None:
        return await _update(pool, request_id, first_approver_id=approver_id)
    if request.first_approver_id == approver_id:
        raise ApprovalError("DUAL 모드는 서로 다른 계정의 순차 서명이 필요합니다.")
    return await _update(
        pool, request_id, status="APPROVED", second_approver_id=approver_id, resolved_at=now
    )


async def reject(pool: asyncpg.Pool, request_id: int, approver_id: UUID) -> ApprovalRequest:
    request = await _fetch(pool, request_id)
    if request.status != "PENDING":
        raise ApprovalError(f"이미 처리된 요청: status={request.status}")
    return await _update(
        pool, request_id, status="REJECTED", resolved_at=datetime.now(timezone.utc)
    )


async def cancel(pool: asyncpg.Pool, request_id: int) -> ApprovalRequest:
    """9.4b/9.6 — 대기 중 조건 재악화 시 자동 취소(악화된 상태로 재가동되는
    경로 원천 차단)."""
    return await _update(
        pool, request_id, status="CANCELLED", resolved_at=datetime.now(timezone.utc)
    )


async def expire_pending(pool: asyncpg.Pool) -> list[int]:
    """FD-10.1 예외상황 — 타이머 만료까지 아무도 응답하지 않으면 자동 거부
    (fail-safe, 암묵적 승인 없음). 주기적으로(예: 안전 루프에서) 호출한다."""
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
