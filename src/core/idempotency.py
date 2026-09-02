"""15번 §15.1 — 금전 관련 POST의 Idempotency-Key 처리.

Spec: 15_api_spec_rbac_v1.6.md#§15.1

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
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

IN_PROGRESS_STATUS_CODE = 0
CONFLICT_STATUS_CODE = 409
CONFLICT_BODY: dict[str, Any] = {
    "detail": "동일 Idempotency-Key 요청이 아직 처리 중입니다. 잠시 후 다시 시도하세요."
}


def _is_cacheable(status_code: int) -> bool:
    return 200 <= status_code < 300


async def with_idempotency(
    pool: asyncpg.Pool,
    key: str,
    compute: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any]]:
    """compute()는 (status_code, response_body)를 반환해야 한다.

    - 이 key로 성공(2xx) 처리된 적이 있으면 compute()를 실행하지 않고 캐시된
      결과를 반환한다.
    - 같은 key가 지금 처리 중이면 (409, CONFLICT_BODY)를 반환한다.
    - compute()가 2xx가 아닌 결과를 내거나 예외를 던지면 선점 행을 지워
      같은 key로 재시도할 수 있게 한다.
    """
    async with pool.acquire() as conn:
        claimed = await conn.fetchval(
            "INSERT INTO idempotency_keys (key, status_code, response_body) "
            "VALUES ($1, $2, '{}'::jsonb) ON CONFLICT (key) DO NOTHING RETURNING key",
            key,
            IN_PROGRESS_STATUS_CODE,
        )
        if claimed is None:
            cached = await conn.fetchrow(
                "SELECT status_code, response_body FROM idempotency_keys WHERE key = $1", key
            )
            if cached is None or cached["status_code"] == IN_PROGRESS_STATUS_CODE:
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
