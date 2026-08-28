"""11.1 이후 공용 테스트 유틸.

users 테이블에 여러 테이블의 user_id FK가 연결된 이후, 그 테이블에 행을
넣는 통합 테스트는 실제 users 행이 먼저 있어야 한다(FK 위반 방지). 각
호출마다 고유 이메일로 별도 사용자를 만들어 테스트 간 격리를 유지한다.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg


async def create_test_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
            f"test-{uuid4().hex}@example.com",
            "test-hash",
        )
    user_id: UUID = row["user_id"]
    return user_id
