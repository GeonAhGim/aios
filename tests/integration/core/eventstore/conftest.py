"""FA-13 `append.py` 실 DB(TEST_DATABASE_URL) 통합테스트 공용 픽스처.

이 리프는 마이그레이션을 만들지 않는다(task-1703 decision) — `event_store`
테이블은 영구 스키마가 아니라 이 테스트 세션 안에서만 존재하는 임시
테이블이다. 매 테스트 전에 만들고 끝나면 지운다(alembic 마이그레이션과
독립적으로 동작해야 다른 워커의 `alembic upgrade head` 상태와 충돌하지
않는다).
"""
from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.eventstore.append import TABLE

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_CREATE_TABLE_SQL = f"""
CREATE TABLE {TABLE} (
    id BIGSERIAL PRIMARY KEY,
    stream_id VARCHAR NOT NULL,
    seq INTEGER NOT NULL,
    type VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    causation_id VARCHAR,
    correlation_id VARCHAR,
    hash VARCHAR NOT NULL,
    prev_hash VARCHAR,
    UNIQUE (stream_id, seq)
)
"""


def _asyncpg_dsn() -> str:
    env = dotenv_values(_PROJECT_ROOT / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=25)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def event_store_table(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        await conn.execute(_CREATE_TABLE_SQL)
    yield
    async with pool.acquire() as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
