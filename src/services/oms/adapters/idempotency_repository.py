"""`order_idempotency` Postgres 어댑터(L4 명세 §9 L4-08).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §5.1
("`INSERT ... ON CONFLICT (scope_hash) DO NOTHING RETURNING`, 없으면 SELECT
후 digest 비교"), §5.2(스코프·digest).

`src/core/idempotency.py`(15번 §15.1, PLT-14)의 claim-first 패턴을 그대로
따르되, 이 테이블만의 `expires_at`(TTL) 재선점을 한 단계 더 추가한다:
1차 INSERT가 충돌하면 기존 행을 읽어 만료 여부를 먼저 본다 — 만료됐으면
"없던 것처럼" `expires_at < now()` 조건부 UPDATE로 재선점(NEW), 그 UPDATE도
경합에서 졌으면(다른 워커가 먼저 재선점) 재조회해 digest를 비교한다.
만료 전이면 그대로 digest 비교 — 같으면 EXISTING, 다르면
`IdempotencyDigestMismatchError`(domain/errors.py 기존 클래스 재사용,
kind="DIGEST_MISMATCH"는 예외로만 신호하고 `ClaimResult`로는 반환하지
않는다 — 호출부가 실수로 무시하지 못하게 fail-closed).

시간 비교는 전부 Postgres `now()`로 한다(애플리케이션-DB 시계 드리프트를
피한다) — `ttl`은 `make_interval(secs => ...)`로 변환.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import asyncpg

from src.services.oms.domain.errors import IdempotencyDigestMismatchError
from src.services.oms.ports.repository import ClaimResult

_INSERT_SQL = (
    "INSERT INTO order_idempotency (scope_hash, digest, order_id, expires_at) "
    "VALUES ($1, $2, $3, now() + make_interval(secs => $4::double precision)) "
    "ON CONFLICT (scope_hash) DO NOTHING RETURNING order_id"
)
_SELECT_SQL = (
    "SELECT digest, order_id, (expires_at < now()) AS expired "
    "FROM order_idempotency WHERE scope_hash = $1"
)
_RECLAIM_SQL = (
    "UPDATE order_idempotency SET digest = $2, order_id = $3, "
    "expires_at = now() + make_interval(secs => $4::double precision) "
    "WHERE scope_hash = $1 AND expires_at < now() RETURNING order_id"
)
_MAX_ATTEMPTS = 3  # 만료 재선점 경합 재조회 상한 — 정상 경로는 1~2회로 끝난다.


class IdempotencyRepository:
    """`IdempotencyRepoPort` 구현체 — I/O 전부 이 클래스 안에만 있다."""

    async def claim(
        self,
        conn: asyncpg.Connection,
        *,
        scope_hash: str,
        digest: str,
        order_id: UUID,
        ttl: timedelta,
    ) -> ClaimResult:
        ttl_sec = ttl.total_seconds()
        inserted = await conn.fetchval(_INSERT_SQL, scope_hash, digest, order_id, ttl_sec)
        if inserted is not None:
            return ClaimResult(kind="NEW", order_id=inserted)

        for _ in range(_MAX_ATTEMPTS):
            existing = await conn.fetchrow(_SELECT_SQL, scope_hash)
            if existing is None:
                # 동시 만료청소/삭제 경합 — 처음부터 다시 선점 시도.
                inserted = await conn.fetchval(
                    _INSERT_SQL, scope_hash, digest, order_id, ttl_sec
                )
                if inserted is not None:
                    return ClaimResult(kind="NEW", order_id=inserted)
                continue
            if existing["expired"]:
                reclaimed = await conn.fetchval(
                    _RECLAIM_SQL, scope_hash, digest, order_id, ttl_sec
                )
                if reclaimed is not None:
                    return ClaimResult(kind="NEW", order_id=reclaimed)
                continue  # 다른 워커가 먼저 재선점 — 다시 읽어 판단.
            if existing["digest"] != digest:
                raise IdempotencyDigestMismatchError(scope_hash)
            return ClaimResult(kind="EXISTING", order_id=existing["order_id"])

        raise RuntimeError(
            f"order_idempotency scope_hash={scope_hash}: 만료 재선점 경합이 "
            f"{_MAX_ATTEMPTS}회 안에 정착하지 않았습니다."
        )
