"""L0-5 — DB 역할 분리(aios_migrator/aios_app) + WORM 트리거 소급 적용 +
`/metrics` 통합 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5
DoD: 역할 존재, `aios_app`으로 `audit_log` UPDATE 실패, `/metrics`는 토큰이
있으면 200(task-939/PLT-09부터 토큰 필수 — 미토큰 403은 별도 파일에서 검증).

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


def _assert_append_only_violation(exc_info: pytest.ExceptionInfo) -> None:
    """`aios_app`의 쓰기는 REVOKE·WORM 트리거 두 방어층 중 하나로 막힌다.

    어느 쪽이 먼저 발동하는지(`asyncpg.InsufficientPrivilegeError` vs
    `asyncpg.RaiseError`)는 PostgreSQL 권한 검사와 트리거 실행 순서에 달려 있고
    이 순서는 계약이 아니다 — "쓰기가 막힌다"만 계약이므로 트리거가 실제로
    발동한 경우(RaiseError)에 한해 그 메시지가 WORM 가드인지 확인한다.
    """
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


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

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE audit_log SET actor_agent = 'tampered' WHERE log_id = $1", log_id
            )
    _assert_append_only_violation(exc_info)


async def test_aios_app_cannot_delete_audit_log(conn):
    row = await conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log.delete', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("DELETE FROM audit_log WHERE log_id = $1", log_id)
    _assert_append_only_violation(exc_info)


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

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                "UPDATE foundation_audit_event SET outcome = 'DENIED' WHERE id = $1", event_id
            )
    _assert_append_only_violation(exc_info)


async def test_aios_app_cannot_update_wallet_transactions(conn):
    user_id = await _insert_user(conn)
    row = await conn.fetchrow(
        "INSERT INTO wallet_transactions (user_id, tx_type, amount, balance_after) "
        "VALUES ($1, 'TOPUP', 100, 100) RETURNING id",
        user_id,
    )
    tx_id = row["id"]

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
        async with conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute("UPDATE wallet_transactions SET amount = 999 WHERE id = $1", tx_id)
    _assert_append_only_violation(exc_info)


async def test_worm_trigger_blocks_table_owner_on_audit_log(conn):
    """REVOKE는 테이블 소유자에게 적용되지 않는다(PostgreSQL 원칙) — 그래서 실제
    강제 수단은 트리거([[src/core/db/append_only.py]] 참고)뿐이다. `conn`은
    `SET ROLE` 없이 접속하며 `audit_log`를 실제로 소유하고 있으므로(마이그레이션을
    이 계정으로 실행했다), 여기서 UPDATE가 막힌다면 REVOKE 우회 여부와 무관하게
    트리거 자체가 살아 있다는 뜻이다.

    Spec: L0-3 '소유자도 우회 불가'.
    """
    row = await conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log.owner', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with conn.transaction():
            await conn.execute(
                "UPDATE audit_log SET actor_agent = 'tampered' WHERE log_id = $1", log_id
            )


async def test_metrics_endpoint_returns_200_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """task-939(PLT-09) — `/metrics`는 `AIOS_METRICS_TOKEN` 없이는 403(별도
    tests/integration/api/test_health_endpoints.py에서 검증). 이 파일의 DoD는
    "토큰이 있으면 여전히 200"만 확인한다."""
    monkeypatch.setenv("AIOS_METRICS_TOKEN", "test-suite-token")

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/metrics", headers={"X-Metrics-Token": "test-suite-token"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
