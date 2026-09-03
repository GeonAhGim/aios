"""L4 §2.3(C) §3.7 — 금전 관련 POST의 Idempotency-Key 헤더 규격.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.7 IdempotencyScope, §9 PLT-14

헤더 규격·digest 계산 규칙은 프론트가 이미 구현한 §3.7 계약 그대로다
(task-427 `canonicalDigest.ts`의 `canonicalJson`+`sha256Hex`) — 새 규격을
만들지 않는다: 키 정렬 + 공백 없는 JSON 직렬화 후 sha256 hex(64자).

라우트 이관(PLT-15, `Idempotency-Replayed` 응답 헤더 배선 포함)은 이 모듈
밖이다 — 헤더 파싱·digest 계산·`core/idempotency.py` 위의 digest 대조까지만.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.contracts.error_codes import ErrorCode
from src.api.foundation_deps import get_tenant_context
from src.core.idempotency import DigestMismatchError, with_idempotency
from src.foundation.trust.contracts.v1 import TenantContext

HEADER_NAME = "Idempotency-Key"
_HEADER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class IdempotencyScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    header_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    route: str
    tenant_id: UUID
    subject_id: UUID
    digest: str = Field(min_length=64, max_length=64)

    @property
    def storage_key(self) -> str:
        return f"{self.route}:{self.tenant_id}:{self.subject_id}:{self.header_key}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def canonical_json(body: Any) -> str:
    """키 정렬 + 공백 없는 결정적 직렬화(frontend `canonicalJson`과 동형)."""
    return json.dumps(_canonicalize(body), sort_keys=True, separators=(",", ":"))


def compute_body_digest(body: Any) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


async def require_idempotency_key(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
) -> IdempotencyScope:
    """`Idempotency-Key` 헤더(16~128자, `[A-Za-z0-9_-]`)를 검증하고 요청
    본문 digest와 함께 `IdempotencyScope`를 만든다. 헤더가 없거나 형식이
    틀리면 400 `VALIDATION_IDEMPOTENCY_KEY_REQUIRED`."""
    header_value = request.headers.get(HEADER_NAME)
    if header_value is None or not _HEADER_PATTERN.match(header_value):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "error_code": ErrorCode.VALIDATION_IDEMPOTENCY_KEY_REQUIRED.value,
                "message": f"{HEADER_NAME} 헤더가 필요합니다(16~128자, [A-Za-z0-9_-]).",
            },
        )

    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "error_code": ErrorCode.VALIDATION_INVALID_FIELD.value,
                "message": "요청 본문이 올바른 JSON이 아닙니다.",
            },
        ) from exc

    route = request.scope.get("route")
    route_name = route.name if route is not None else request.url.path

    return IdempotencyScope(
        header_key=header_value,
        route=route_name,
        tenant_id=ctx.tenant_id,
        subject_id=ctx.subject_id,
        digest=compute_body_digest(body),
    )


async def run_idempotent(
    pool: asyncpg.Pool,
    scope: IdempotencyScope,
    compute: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any]]:
    """`with_idempotency` 위에 digest 대조를 얹는다 — 같은 `Idempotency-Key`로
    다른 본문이 재전송되면 409 `INTEGRITY_IDEMPOTENCY_CONFLICT`(I8)."""
    try:
        return await with_idempotency(
            pool,
            scope.storage_key,
            compute,
            tenant_id=scope.tenant_id,
            digest=scope.digest,
        )
    except DigestMismatchError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error_code": ErrorCode.INTEGRITY_IDEMPOTENCY_CONFLICT.value,
                "message": "동일 Idempotency-Key로 다른 요청 본문이 재전송됐습니다.",
            },
        ) from exc
