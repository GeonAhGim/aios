"""3.x — 작업트리 섹션 3(DB 스키마) 통합 테스트 공용 fixture·헬퍼.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다: `alembic upgrade head`.

RATCHET-split(task-4222) — 원래 `test_db_schema.py`(1176줄) 단일 파일을
`tests/integration/db_schema/test_*.py` 여러 파일로 나누며, `db_conn`/
`raw_conn` fixture는 pytest conftest 자동탐색으로 공유한다(각 테스트
파일에서 이름을 다시 import할 필요가 없다 — import하면 ruff F811
redefinition으로 걸린다).
"""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _database_url() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url


@pytest.fixture
async def db_conn():
    # 이벤트 루프마다 새 엔진 필요 — pytest-asyncio가 테스트별 새 루프를 만들고
    # asyncpg 커넥션은 루프에 종속되기 때문(NullPool로 커넥션 재사용 방지).
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest.fixture
async def raw_conn():
    """LC-6 트랜잭션·롤 테스트용 — deferred 트리거의 커밋 시점 동작과
    `SET ROLE`은 asyncpg 원시 커넥션(`test_db_roles.py`와 동일 패턴)으로만
    직접 검증할 수 있다(SQLAlchemy `AsyncConnection`은 커밋 시점을 감춘다)."""
    dsn = _database_url().replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    yield connection
    await connection.close()


async def insert_audit_event(conn: asyncpg.Connection) -> object:
    row = await conn.fetchrow(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.ledger', gen_random_uuid(), 'test.ledger.post', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid4().int % (2**62),  # system(tenant_id IS NULL) sequence_no 유일 제약 회피용 난수
    )
    return row["id"]


async def insert_ledger_entry(conn: asyncpg.Connection, *, audit_event_id: object) -> object:
    row = await conn.fetchrow(
        "INSERT INTO ledger_journal_entry "
        "(sequence_no, event_type, event_ref, idempotency_key, lines_digest, entry_hash, "
        " audit_event_id) "
        "VALUES ($1, 'MANUAL_ADJUSTMENT', $2, $3, repeat('0', 64), repeat('0', 64), $4) "
        "RETURNING entry_id",
        uuid4().int % (2**62) + 1,
        f"test:{uuid4().hex}",
        f"MANUAL_ADJUSTMENT:test:{uuid4().hex}",
        audit_event_id,
    )
    return row["entry_id"]
