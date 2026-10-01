"""3.x — 작업트리 섹션 3(DB 스키마) 통합 테스트 공용 fixture·헬퍼.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다: `alembic upgrade head`.

RATCHET-split(task-4222) — 원래 `test_db_schema.py`(1176줄) 단일 파일을
`tests/integration/db_schema/test_*.py` 여러 파일로 나누며, `db_conn`/
`raw_conn` fixture는 pytest conftest 자동탐색으로 공유한다(각 테스트
파일에서 이름을 다시 import할 필요가 없다 — import하면 ruff F811
redefinition으로 걸린다).
"""

from collections.abc import AsyncGenerator as AbcAsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


def _database_url() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url


async def _db_conn_impl() -> AbcAsyncGenerator["AsyncConnection", None]:
    # 이벤트 루프마다 새 엔진 필요 — pytest-asyncio가 테스트별 새 루프를 만들고
    # asyncpg 커넥션은 루프에 종속되기 때문(NullPool로 커넥션 재사용 방지).
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest.fixture
async def db_conn() -> AbcAsyncGenerator["AsyncConnection", None]:
    async for conn in _db_conn_impl():
        yield conn


async def _raw_conn_impl() -> AbcAsyncGenerator[asyncpg.Connection, None]:
    dsn = _database_url().replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    yield connection
    await connection.close()


@pytest.fixture
async def raw_conn() -> AbcAsyncGenerator[asyncpg.Connection, None]:
    """LC-6 트랜잭션·롤 테스트용 — deferred 트리거의 커밋 시점 동작과
    `SET ROLE`은 asyncpg 원시 커넥션(`test_db_roles.py`와 동일 패턴)으로만
    직접 검증할 수 있다(SQLAlchemy `AsyncConnection`은 커밋 시점을 감춘다)."""
    async for conn in _raw_conn_impl():
        yield conn


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


# --- DEEPEN(task-10250): conftest 헬퍼/fixture 자체의 negative/실패주입 ------
# 원 리프(task-6704)는 이 공용 fixture 파일에 negative test가 0건이었다 —
# `db_conn`/`raw_conn`/`insert_*` 헬퍼가 잘못된 입력·의존성 장애를 제대로
# 거부/전파하는지는 그동안 어느 `test_*.py`도 직접 검증하지 않았다.


def test_database_url_missing_raises_assertion(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env`에 `DATABASE_URL`이 없으면 모든 통합 테스트가 알 수 없는 접속
    오류 대신 즉시 분명한 `AssertionError`로 fail-closed 해야 한다."""
    monkeypatch.setattr("tests.integration.db_schema.conftest.dotenv_values", lambda *_a, **_k: {})
    with pytest.raises(AssertionError, match="DATABASE_URL"):
        _database_url()


async def test_insert_ledger_entry_rejects_unknown_audit_event_id(
    raw_conn: asyncpg.Connection,
) -> None:
    """`ledger_journal_entry.audit_event_id`는 `foundation_audit_event(id)`
    FK다 — 존재하지 않는 감사 이벤트를 가리키는 분개는 거부되어야 한다
    (LC-9 post_entry가 감사 추적 없는 분개를 남기지 않도록 하는 최후 방어선)."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await insert_ledger_entry(raw_conn, audit_event_id=uuid4())


async def test_ledger_journal_entry_sequence_no_zero_rejected(
    raw_conn: asyncpg.Connection,
) -> None:
    """`sequence_no >= 1` CHECK negative — 0 이하의 sequence_no는 해시체인
    선두 센티널과 충돌할 수 있어 DB 레벨에서 거부되어야 한다."""
    audit_event_id = await insert_audit_event(raw_conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO ledger_journal_entry "
            "(sequence_no, event_type, event_ref, idempotency_key, lines_digest, entry_hash, "
            " audit_event_id) "
            "VALUES (0, 'MANUAL_ADJUSTMENT', $1, $2, repeat('0', 64), repeat('0', 64), $3)",
            f"test:{uuid4().hex}",
            f"MANUAL_ADJUSTMENT:test:{uuid4().hex}",
            audit_event_id,
        )


async def test_foundation_audit_event_duplicate_system_sequence_no_rejected(
    raw_conn: asyncpg.Connection,
) -> None:
    """`uq_foundation_audit_event_system_seq` negative — tenant_id가 NULL인
    system 이벤트는 일반 UNIQUE(tenant_id, sequence_no)로 안 잡히므로
    (Postgres는 NULL끼리 다르다고 취급) 추가한 partial index가 실제로
    같은 sequence_no 재사용을 막는지 직접 확인한다."""
    sequence_no = uuid4().int % (2**62) + 2
    insert_sql = (
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.ledger', gen_random_uuid(), 'test.ledger.post', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef')"
    )
    await raw_conn.execute(insert_sql, sequence_no)
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_conn.execute(insert_sql, sequence_no)


async def test_db_conn_impl_propagates_engine_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 — `create_async_engine`이 던지는 예외(예: 드라이버 설치
    누락, DSN 파싱 실패)가 `db_conn` fixture에 삼켜지지 않고 그대로
    전파되는지 monkeypatch로 강제 확인한다."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("engine creation failed")

    monkeypatch.setattr("tests.integration.db_schema.conftest.create_async_engine", _boom)
    with pytest.raises(RuntimeError, match="engine creation failed"):
        async for _ in _db_conn_impl():
            pass
