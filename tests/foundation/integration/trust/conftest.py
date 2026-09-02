from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg


def unique_purpose() -> str:
    """disclosure.purpose에는 UNIQUE(purpose, revision) 제약이 있어, 테스트 간
    격리를 위해 매번 새 purpose를 쓴다(다른 통합테스트의 `f"test-strategy-
    {uuid4().hex[:8]}"` 패턴과 동일 원칙)."""
    return f"test-purpose-{uuid4().hex[:8]}"


async def create_disclosure(pool: asyncpg.Pool, *, purpose: str, revision: int = 1) -> UUID:
    """disclosure는 운영자가 발행하는 컨텐츠이고 FND-01 사용자 커맨드 범위 밖이라
    (71번 §6 엔드포인트 목록에 없음), 테스트는 이 헬퍼로 직접 행을 만든다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO disclosure (purpose, revision, content_hash) "
            "VALUES ($1, $2, $3) RETURNING id",
            purpose,
            revision,
            f"hash-{uuid4().hex[:8]}",
        )
    disclosure_id: UUID = row["id"]
    return disclosure_id


async def retire_disclosure(pool: asyncpg.Pool, disclosure_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE disclosure SET retired_at = now() WHERE id = $1", disclosure_id
        )
