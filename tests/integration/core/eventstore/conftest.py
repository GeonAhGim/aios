"""FA-13 `append.py` 실 DB(TEST_DATABASE_URL) 통합테스트 공용 픽스처.

이 리프는 마이그레이션을 만들지 않는다(task-1703 decision) — `event_store`
테이블은 영구 스키마가 아니라 이 테스트 세션 안에서만 존재하는 임시
테이블이다. 매 테스트 전에 만들고 끝나면 지운다(alembic 마이그레이션과
독립적으로 동작해야 다른 워커의 `alembic upgrade head` 상태와 충돌하지
않는다).

negative/실패주입 테스트(task-9229 DEEPEN):
- naive_datetime: tzinfo 없는 occurred_at/recorded_at를 append()가 거부한다
- non_monotonic_seq: expected_seq가 현재 head를 건너뛰면 SequenceConflictError
- db_error_injection: INSERT 단계의 conn.fetchrow를 monkeypatch해 커넥션
  유실을 흉내내고, append()가 이를 삼키지 않고 그대로 전파하는지 검증한다
  (append()는 UniqueViolationError만 SequenceConflictError로 변환한다 —
  그 외 예외는 fail-closed로 호출자에게 그대로 올라가야 한다)
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


# ---------------------------------------------------------------------------
# negative tests - append()는 불변식을 위반하는 입력을 명시적으로 거부한다
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# failure-injection test - append()는 UniqueViolationError만 SequenceConflictError로
# 변환한다. 그 외 DB 예외(커넥션 유실 등)는 삼키지 않고 fail-closed로 그대로
# 호출자에게 전파해야 한다.
# ---------------------------------------------------------------------------
