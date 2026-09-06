"""10.1 — ApprovalRequest 모델/공용 조회 헬퍼.

Spec: 기능설계문서_v1.20.md#FD-10.1, ADR-2026-08-10-D

task-1723 P1-D: service.py(303줄, P6 300줄 초과) 분할로 생긴 공용 모듈 —
service.py(생성/조회)와 resolution.py(승인/거절/취소/만료) 양쪽이 이 모듈의
`ApprovalRequest`/`ApprovalError`/`_row_to_model`/`_fetch`를 가져다 쓴다(순환
import 방지를 위해 별도 파일로 둠). 공개 경로는 여전히
`src.core.approval.service`다 — 그 모듈이 이 심볼들을 재수출한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel


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
