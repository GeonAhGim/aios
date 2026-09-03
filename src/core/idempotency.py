"""15번 §15.1 — 금전 관련 POST의 Idempotency-Key 처리.

Spec: 15_api_spec_rbac_v1.6.md#§15.1
PLT-14: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9

동일 Idempotency-Key로 재요청이 오면 새 부작용(중복 구매 등)을 만들지
않고 최초 성공 응답을 그대로 캐시해 반환한다.

전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 이전 구현의 세 결함을
한 번에 고친다.

1. **키 스코프**: 키 문자열 자체는 호출부가 만들되, 반드시 사용자 식별자를
   포함해야 한다(마켓플레이스 라우터는 ``purchase:{user_id}:{header}``).
   이전에는 헤더값만으로 키를 만들어 다른 사용자가 같은 헤더값을 보내면
   남의 구매 응답을 그대로 받았다.
2. **실패 응답 미캐시**: 4xx/5xx는 저장하지 않는다. 이전에는 잔액 부족
   402도 캐시돼 충전 후 같은 키로 재시도하면 영원히 402가 나왔다.
3. **claim-first 원자성**: 처리 전에 자리표시자 행(status_code=0)을
   ``INSERT ... ON CONFLICT DO NOTHING``으로 먼저 선점한다. 같은 키의 두
   요청이 동시에 도착하면 한쪽만 compute()를 실행하고 다른 쪽은 409를
   받는다(order_service/submit.py의 claim-then-send와 같은 원칙). 선점
   직후 커넥션을 반납하므로 compute() 안에서 풀을 다시 잡아도 풀 고갈로
   교착하지 않는다.

PLT-14(M2 `idempotency_keys_scope_digest`) 추가분 — ``tenant_id``·
``request_digest``·``expires_at`` 컬럼과 I8 불변조건("같은 Idempotency-Key
+ 다른 digest는 409")을 이 계층에서 강제한다. ``tenant_id``/``digest``는
호출부가 안 넘기면(``None``) 예전처럼 digest 대조 없이 동작한다 —
`tests/integration/test_idempotency.py`(15 §15.1 최초 구현, PLT-14 이전)가
무수정으로 계속 통과해야 하기 때문이다. digest 대조 자체가 필요한
호출부(`src/api/contracts/idempotency.py`)는 항상 두 값을 넘긴다.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg

IN_PROGRESS_STATUS_CODE = 0
CONFLICT_STATUS_CODE = 409
CONFLICT_BODY: dict[str, Any] = {
    "detail": "동일 Idempotency-Key 요청이 아직 처리 중입니다. 잠시 후 다시 시도하세요."
}


class DigestMismatchError(Exception):
    """같은 key로 이전과 다른 ``request_digest``가 재전송됐다(I8). fastapi에
    의존하지 않는 core 계층이라 여기서는 그냥 예외만 던지고, HTTP 409
    `INTEGRITY_IDEMPOTENCY_CONFLICT`로의 매핑은 호출부(계약 계층)의 몫이다."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key={key!r}: request_digest가 기존과 다릅니다.")
        self.key = key


def _is_cacheable(status_code: int) -> bool:
    return 200 <= status_code < 300


async def with_idempotency(
    pool: asyncpg.Pool,
    key: str,
    compute: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    *,
    tenant_id: UUID | None = None,
    digest: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """compute()는 (status_code, response_body)를 반환해야 한다.

    - 이 key로 성공(2xx) 처리된 적이 있으면 compute()를 실행하지 않고 캐시된
      결과를 반환한다.
    - 같은 key가 지금 처리 중이면 (409, CONFLICT_BODY)를 반환한다.
    - ``digest``를 넘겼는데 기존에 저장된 ``request_digest``와 다르면(둘 다
      NULL이 아닐 때만) 처리 중/완료 여부와 무관하게 `DigestMismatchError`를
      던진다 — 같은 키를 다른 본문으로 재사용하는 호출자 버그는 캐시된
      응답이 아직 없어도 즉시 막아야 한다.
    - compute()가 2xx가 아닌 결과를 내거나 예외를 던지면 선점 행을 지워
      같은 key로 재시도할 수 있게 한다.
    """
    async with pool.acquire() as conn:
        claimed = await conn.fetchval(
            "INSERT INTO idempotency_keys "
            "(key, status_code, response_body, tenant_id, request_digest) "
            "VALUES ($1, $2, '{}'::jsonb, $3, $4) ON CONFLICT (key) DO NOTHING RETURNING key",
            key,
            IN_PROGRESS_STATUS_CODE,
            tenant_id,
            digest,
        )
        if claimed is None:
            cached = await conn.fetchrow(
                "SELECT status_code, response_body, request_digest FROM idempotency_keys "
                "WHERE key = $1",
                key,
            )
            if cached is None:
                return CONFLICT_STATUS_CODE, dict(CONFLICT_BODY)
            stored_digest = cached["request_digest"]
            if digest is not None and stored_digest is not None and stored_digest != digest:
                raise DigestMismatchError(key)
            if cached["status_code"] == IN_PROGRESS_STATUS_CODE:
                return CONFLICT_STATUS_CODE, dict(CONFLICT_BODY)
            return cached["status_code"], json.loads(cached["response_body"])

    try:
        status_code, response_body = await compute()
    except BaseException:
        await _release(pool, key)
        raise

    if _is_cacheable(status_code):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE idempotency_keys SET status_code = $2, response_body = $3::jsonb "
                "WHERE key = $1",
                key,
                status_code,
                json.dumps(response_body),
            )
    else:
        await _release(pool, key)
    return status_code, response_body


async def _release(pool: asyncpg.Pool, key: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM idempotency_keys WHERE key = $1", key)


async def purge_expired(pool: asyncpg.Pool) -> int:
    """``expires_at``이 지난 행을 지운다. 삭제된 행 수를 반환한다(배치
    작업이 호출 — 이 모듈 자체는 스케줄러를 갖지 않는다)."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM idempotency_keys WHERE expires_at < now()")
    return int(result.split()[-1])
