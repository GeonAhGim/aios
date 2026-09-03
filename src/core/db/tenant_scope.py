"""PLT-30 — RLS 세션 변수 바인딩: `tenant_transaction()`이 유일한 지점.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2(108행),
§9 PLT-30. §10 리스크1 — 이 모듈이 여는 트랜잭션 밖(`pool.acquire()`만 쓰는
기존 40여 서비스)에서는 `app.tenant_id`가 설정되지 않으므로, RLS가 걸린
테이블(M5가 ENABLE한 foundation 8개)을 그 경로로 조회하면 0행이 된다 —
그래서 M5는 그 8개로만 범위를 좁혔다(레거시 테이블은 정책만 만들고
ENABLE하지 않음).

`SET LOCAL`은 트랜잭션 범위라 커밋/롤백 어느 쪽이든 트랜잭션이 끝나면 자동
원복된다(PostgreSQL 문서) — 커넥션이 풀로 반환된 뒤에도 값이 남지 않는다.
문자열 결합 대신 `set_config(name, value, true)`를 바인드 파라미터로 호출해
tenant_id를 전달한다(인젝션 여지 없음, 세 번째 인자 `true`가 LOCAL과 동등).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


@asynccontextmanager
async def tenant_transaction(
    pool: asyncpg.Pool, tenant_id: UUID | None
) -> AsyncIterator[asyncpg.Connection]:
    """`app.tenant_id`를 트랜잭션 범위로 묶은 커넥션을 연다.

    `tenant_id=None`이면 빈 문자열을 묶는다 — 어떤 tenant 소유 행에도
    매치하지 않아(fail-closed) 정책이 항상 0행을 반환한다. system 이벤트
    (`tenant_id IS NULL`)까지 읽으려면 [[system_transaction]]을 대신 쓴다.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)",
            "" if tenant_id is None else str(tenant_id),
        )
        yield conn


@asynccontextmanager
async def system_transaction(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """`app.role='system'`만 묶는다(`app.tenant_id`는 빈 문자열 그대로).

    M5 정책 중 `tenant_id IS NULL AND current_setting('app.role', true) =
    'system'` 분기(foundation_audit_event 전용)만 추가로 통과시키고, tenant
    소유 행은 [[tenant_transaction]](None)과 마찬가지로 여전히 0행이다.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', '', true)")
        await conn.execute("SELECT set_config('app.role', 'system', true)")
        yield conn
