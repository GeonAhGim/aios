"""L4 §2.3(C) — 페이지네이션 파라미터/메타 계약.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

커서 방식(CursorParams)은 카운트 비용이 큰 목록(§3.3 PageMeta.total의
"카운트 비용이 큰 목록은 None 허용" 주석)을 위한 것 — 이번 리프(PLT-108,
auth/users/admin 라우터까지)는 페이지 번호 방식(PageParams)만 실제로
쓴다. encode_cursor/decode_cursor는 커서 방식을 실제로 채택하는 라우터가
생기는 시점에 필요해질 것이라 지금은 만들지 않는다(과잉설계 방지,
17.9-A 원칙 — 이 세션 다른 곳에서도 반복 적용됨).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class PageMeta(BaseModel):
    total: int | None = None
    page: int | None = None
    size: int
    next_cursor: str | None = None
