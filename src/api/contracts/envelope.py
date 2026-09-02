"""L4 §2.3(C) — API 응답 봉투.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

trace_id는 별도 "관측성" 모듈(context.py, 이번 리프 스콥 밖)을 새로
만들지 않고 이미 있는 request_id 미들웨어(src/core/logging/
request_context.py, PLT-107)를 그대로 재사용한다 — 같은 값이 응답
헤더 X-Request-ID와 봉투 meta.trace_id 양쪽에 찍혀야 클라이언트가
장애 신고 시 둘 중 뭘 봐도 같은 요청을 가리킨다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.api.contracts.pagination import PageMeta
from src.core.logging.request_context import get_current_request_id

T = TypeVar("T")


def current_trace_id() -> UUID:
    """request_id 미들웨어가 설정한 값(32자리 hex)을 UUID로 파싱한다.
    HTTP 요청 컨텍스트 밖(배치 작업 등)에서 호출되면 그 자리에서 새
    UUID를 하나 만든다 — trace_id가 아예 없는 것보다는 이번 호출 하나만
    가리키는 값이라도 있는 게 로그 추적에 낫다."""
    raw = get_current_request_id()
    if raw is None:
        return uuid4()
    return UUID(hex=raw)


class Meta(BaseModel):
    trace_id: UUID
    as_of: datetime
    page: PageMeta | None = None


class ApiResponse(BaseModel, Generic[T]):
    data: T
    meta: Meta


class ApiError(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: UUID
    retry_after_seconds: int | None = None


def ok(data: T, *, page: PageMeta | None = None) -> ApiResponse[T]:
    return ApiResponse(
        data=data,
        meta=Meta(trace_id=current_trace_id(), as_of=datetime.now(timezone.utc), page=page),
    )
