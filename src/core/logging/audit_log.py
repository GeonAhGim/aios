"""7.4 — audit_log 기록 유틸 (FD-7.2).

Spec: 04_db_schema_v1.7.md (Audit Log, WORM), 01_data_models_v1.4.md#§1.6
(Decimal↔JSONB 직렬화 원칙),
docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-07
(trace_id 컬럼 기록).

이 함수는 audit_log 테이블에 INSERT만 한다 — WORM 테이블이므로 이 모듈
어디에도 UPDATE/DELETE 경로를 만들지 않는다(DB 레벨 REVOKE로도 이중 방어,
04번 §v1.6). db/session.py(SQLAlchemy async, 작업트리 16번)가 아직 없어
asyncpg 커넥션을 직접 받는다 — 나중에 SQLAlchemy 세션 계층이 생기면 그
계층의 raw connection을 넘겨주는 것으로 그대로 재사용 가능하다.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from src.core.observability.context import current as current_request_context
from src.data.models.serialization import DecimalSafeEncoder


async def record_audit_log(
    conn: asyncpg.Connection,
    *,
    actor_agent: str,
    action_type: str,
    decision_data: dict[str, Any],
    user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    verification_chain: dict[str, Any] | None = None,
    trace_id: UUID | None = None,
) -> None:
    """decision_data/verification_chain에 Decimal이 섞여 있어도 안전하게
    직렬화한다(DecimalSafeEncoder, 01번 §1.6) — 정밀도 손실 방지를 위해
    float으로 변환하지 않고 문자열로 직렬화.

    `trace_id`를 명시하지 않으면 현재 요청 컨텍스트(PLT-01
    `src.core.observability.context.current()`)의 값을 쓴다 — 호출부가
    trace_id를 직접 들고 다니지 않아도 상관관계가 끊기지 않는다."""
    await conn.execute(
        """
        INSERT INTO audit_log
            (user_id, actor_agent, action_type, target_type, target_id,
             decision_data, verification_chain, trace_id)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
        """,
        user_id,
        actor_agent,
        action_type,
        target_type,
        target_id,
        json.dumps(decision_data, cls=DecimalSafeEncoder),
        json.dumps(verification_chain, cls=DecimalSafeEncoder)
        if verification_chain is not None
        else None,
        trace_id if trace_id is not None else current_request_context().trace_id,
    )
