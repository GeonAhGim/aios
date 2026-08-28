"""15번 §15.1 — 금전 관련 POST의 Idempotency-Key 처리.

Spec: 15_api_spec_rbac_v1.6.md#§15.1

동일 Idempotency-Key로 재요청이 오면 새 부작용(중복 구매 등)을 만들지
않고 최초 응답을 그대로 캐시해 반환한다. 캐시 저장/조회 자체가 원자적
INSERT(ON CONFLICT DO NOTHING)로 이루어져 두 요청이 동시에 도착해도
한쪽만 실제로 compute()를 실행한다고 보장하지는 않는다 — Phase 1
스콥에서는 그 정도의 동시성 방어까지는 요구하지 않는다(Draft).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg


async def with_idempotency(
    pool: asyncpg.Pool,
    key: str,
    compute: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any]]:
    """compute()는 (status_code, response_body)를 반환해야 한다. 이미 이
    key로 처리된 적이 있으면 compute()를 다시 실행하지 않고 캐시된 결과를
    그대로 반환한다."""
    async with pool.acquire() as conn:
        cached = await conn.fetchrow(
            "SELECT status_code, response_body FROM idempotency_keys WHERE key = $1", key
        )
        if cached is not None:
            return cached["status_code"], json.loads(cached["response_body"])

        status_code, response_body = await compute()

        await conn.execute(
            "INSERT INTO idempotency_keys (key, status_code, response_body) "
            "VALUES ($1, $2, $3::jsonb) ON CONFLICT (key) DO NOTHING",
            key,
            status_code,
            json.dumps(response_body),
        )
    return status_code, response_body
