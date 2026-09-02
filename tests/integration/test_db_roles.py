"""L0-5 — DB 역할 분리(aios_migrator/aios_app) + WORM 트리거 소급 적용 +
`/metrics` 통합 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5
DoD: 역할 존재, `aios_app`으로 `audit_log` UPDATE 실패, `/metrics` 200.

역할 전환은 `SET ROLE`을 쓴다 — 슈퍼유저(TEST_DATABASE_URL 접속 계정)는
비밀번호 없이도 어떤 역할로나 `SET ROLE`할 수 있고, 트랜잭션 안에서
`SET ROLE`한 뒤 그 트랜잭션이 롤백되면 역할 전환도 함께 되돌아간다(PostgreSQL
문서) — 그래서 각 케이스를 `conn.transaction()` 블록 하나로 감싸고 트리거가
예외를 던지게 해 자동 롤백시키면, 실제 DML이 커밋되지 않으면서도 role 상태가
깨끗이 원복된다.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def conn():
    connection = await asyncpg.connect(_asyncpg_dsn())
    yield connection
    await connection.close()


async def _insert_user(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow(
        "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
        f"test-{uuid4().hex}@example.com",
        "test-hash",
    )
    return str(row["user_id"])


async def test_aios_migrator_and_aios_app_roles_exist(conn):
    rows = await conn.fetch(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('aios_migrator', 'aios_app')"
    )
    assert {row["rolname"] for row in rows} == {"aios_migrator", "aios_app"}


async def test_aios_app_cannot_update_audit_log(conn):
    row = await conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE audit_log SET actor_agent = 'tampered' WHERE log_id = $1", log_id
            )


async def test_aios_app_cannot_delete_audit_log(conn):
    row = await conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log.delete', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("DELETE FROM audit_log WHERE log_id = $1", log_id)


async def test_aios_app_cannot_update_foundation_audit_event(conn):
    row = await conn.fetchrow(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.aggregate', gen_random_uuid(), 'test.action', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid4().int % (2**62),  # system(tenant_id IS NULL) sequence_no 유일 제약 회피용 난수
    )
    event_id = row["id"]

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE foundation_audit_event SET outcome = 'DENIED' WHERE id = $1", event_id
            )


async def test_aios_app_cannot_update_wallet_transactions(conn):
    user_id = await _insert_user(conn)
    row = await conn.fetchrow(
        "INSERT INTO wallet_transactions (user_id, tx_type, amount, balance_after) "
        "VALUES ($1, 'TOPUP', 100, 100) RETURNING id",
        user_id,
    )
    tx_id = row["id"]

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("UPDATE wallet_transactions SET amount = 999 WHERE id = $1", tx_id)


async def test_metrics_endpoint_returns_200():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
