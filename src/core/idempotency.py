"""Spec 15 §15.1 — Idempotency-Key handling for monetary POST requests.

Spec: 15_api_spec_rbac_v1.6.md#§15.1
PLT-14: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9

When a retry arrives with the same Idempotency-Key, return the cached
original success response instead of producing new side effects (e.g. a
duplicate purchase).

Reflects the full audit (docs/FULL_AUDIT_2026-09-02.md §2) — fixes three
defects in the previous implementation at once.

1. **Key scope**: the key string itself is built by the caller, but it must
   include a user identifier (the marketplace router uses
   ``purchase:{user_id}:{header}``). Previously the key was built from the
   header value alone, so another user sending the same header value would
   receive someone else's purchase response.
2. **Failure responses are not cached**: 4xx/5xx are never stored.
   Previously an insufficient-balance 402 was also cached, so retrying with
   the same key after topping up kept returning 402 forever.
3. **Claim-first atomicity**: before processing, a placeholder row
   (status_code=0) is claimed first via
   ``INSERT ... ON CONFLICT DO NOTHING``. If two requests with the same key
   arrive concurrently, only one runs compute() and the other gets a 409
   (same principle as the claim-then-send pattern in
   order_service/submit.py). The connection is released right after the
   claim, so re-acquiring the pool inside compute() won't deadlock from pool
   exhaustion.

PLT-14 addition (M2 `idempotency_keys_scope_digest`) — this layer enforces
the ``tenant_id``, ``request_digest``, ``expires_at`` columns and the I8
invariant ("same Idempotency-Key + different digest => 409"). If the caller
doesn't pass ``tenant_id``/``digest`` (``None``), behavior falls back to the
old digest-less comparison — this is required so that
`tests/integration/test_idempotency.py` (the original §15.1 implementation,
predating PLT-14) keeps passing unmodified. Callers that need digest
comparison (`src/api/contracts/idempotency.py`) always pass both values.
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
    """The same key was resent with a different ``request_digest`` than before
    (I8). This core layer doesn't depend on fastapi, so it just raises the
    exception here; mapping it to HTTP 409
    `INTEGRITY_IDEMPOTENCY_CONFLICT` is the caller's (contract layer's) job."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key={key!r}: request_digest differs from the stored value.")
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
    """compute() must return (status_code, response_body).

    - If this key already resulted in a success (2xx), compute() is not run
      and the cached result is returned instead.
    - If the same key is currently being processed, returns
      (409, CONFLICT_BODY).
    - If ``digest`` is passed and differs from the stored ``request_digest``
      (only when neither is NULL), raises `DigestMismatchError` regardless of
      whether processing is in progress or complete — a caller bug that
      reuses the same key with a different body must be blocked immediately,
      even before any cached response exists.
    - If compute() produces a non-2xx result or raises, the claimed row is
      deleted so the same key can be retried.
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
    """Deletes rows whose ``expires_at`` has passed. Returns the number of
    deleted rows (called by a batch job — this module itself has no
    scheduler)."""
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM idempotency_keys WHERE expires_at < now()")
    return int(result.split()[-1])
