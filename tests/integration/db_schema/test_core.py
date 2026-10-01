"""3.x — DB 스키마 통합 테스트: 섹션 3 기본 테이블/WORM/다자산군 컬럼.

Spec: 04_db_schema_v1.7.md, 06_mvp_scope_v1.3.md#§6.3 DoD
("audit_log 테이블에 WORM 제약(REVOKE UPDATE, DELETE) 적용 확인")

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리. `db_conn`
fixture는 `conftest.py`에서 자동 제공된다.
"""

from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text

from tests.integration.db_schema import conftest as db_schema_conftest

EXPECTED_TABLES = {
    "tasks",
    "capability_tokens",
    "strategies",
    "memory_entries",
    "strategy_memory_refs",
    "orders",
    "positions",
    "reconciliation_events",
    "audit_log",
    "notifications",
    "notification_preferences",
}


async def test_all_section_3_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(EXPECTED_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == EXPECTED_TABLES


async def test_audit_log_worm_revoked_from_public(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = 'audit_log' AND grantee = 'PUBLIC'"
        )
    )
    granted = {row[0] for row in result}
    assert "UPDATE" not in granted
    assert "DELETE" not in granted


async def test_foundation_audit_event_worm_revoked_from_public(db_conn):
    """79번 §1 append-only — FND-03(마이그레이션 4453afe74725)도 legacy
    audit_log와 같은 WORM 강제를 쓴다. 소유자 role에는 REVOKE FROM PUBLIC이
    적용되지 않는다는 PostgreSQL 제약(위 test_audit_log_worm_revoked_from_public
    주석 참조)은 여기도 동일하다 — 그래서 실제 UPDATE 시도가 아니라 카탈로그
    권한만 확인한다."""
    result = await db_conn.execute(
        text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = 'foundation_audit_event' AND grantee = 'PUBLIC'"
        )
    )
    granted = {row[0] for row in result}
    assert "UPDATE" not in granted
    assert "DELETE" not in granted


async def test_tasks_capability_token_fk_wired(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'tasks' AND constraint_name = 'fk_tasks_capability_token'"
        )
    )
    assert result.first() is not None


MULTI_ASSET_COLUMNS = {
    "asset_class",
    "option_type",
    "strike_price",
    "expiry_date",
    "contract_multiplier",
    "underlying_symbol",
}


async def test_orders_and_positions_have_multi_asset_columns(db_conn):
    """ADR-2026-08-28 — 04번 §v1.7 다자산군 확장 컬럼(f5dd798b2e28)."""
    for table in ("orders", "positions"):
        result = await db_conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = ANY(:cols)"
            ),
            {"table": table, "cols": list(MULTI_ASSET_COLUMNS)},
        )
        found = {row[0] for row in result}
        assert found == MULTI_ASSET_COLUMNS, f"{table} missing {MULTI_ASSET_COLUMNS - found}"


# --- DEEPEN(task-10251): negative / failure-injection ----------------------


async def test_aios_app_cannot_update_audit_log(raw_conn: asyncpg.Connection) -> None:
    """negative — `test_audit_log_worm_revoked_from_public`은 카탈로그 권한만
    보지만, 실제 `aios_app` 역할로 UPDATE를 시도해 WORM 가드(REVOKE 또는
    append-only 트리거)가 실제로 발동하는지까지 확인한다(`test_ledger.py`의
    `test_aios_app_cannot_update_ledger_journal_entry`와 동일 패턴)."""
    row = await raw_conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE audit_log SET actor_agent = 'tampered' WHERE log_id = $1", log_id
            )


async def test_aios_app_cannot_delete_audit_log(raw_conn: asyncpg.Connection) -> None:
    """negative — 위 테스트의 DELETE 대구 케이스."""
    row = await raw_conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', 'test.worm.audit_log.delete', '{}'::jsonb) RETURNING log_id"
    )
    log_id = row["log_id"]

    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute("DELETE FROM audit_log WHERE log_id = $1", log_id)


async def test_tasks_required_permission_level_out_of_range_rejected(
    raw_conn: asyncpg.Connection,
) -> None:
    """negative — `required_permission_level`은 0..6 CHECK 제약(a7c02fa80d22)
    이다. 범위 밖 값은 애플리케이션 검증을 우회해도 DB에서 거부되어야 한다."""
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO tasks (objective, assigned_agent, required_permission_level) "
            "VALUES ('test-objective', 'test-agent', 7)"
        )


async def test_tasks_capability_token_fk_violation_rejected(
    raw_conn: asyncpg.Connection,
) -> None:
    """negative — `test_tasks_capability_token_fk_wired`는 제약 존재만 확인하니,
    여기서는 존재하지 않는 `capability_token_id`를 실제로 넣어 FK가 참조
    무결성을 거부하는지까지 확인한다."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await raw_conn.execute(
            "INSERT INTO tasks "
            "(objective, assigned_agent, required_permission_level, capability_token_id) "
            "VALUES ('test-objective', 'test-agent', 1, $1)",
            uuid4(),
        )


async def test_database_url_fails_closed_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 — `.env` 로드가 `DATABASE_URL`을 비운 의존성 장애를 흉내낸다
    (`conftest.dotenv_values`를 몽키패치). §3 표준-105 fail-closed 기본대로
    `_database_url()`은 조용히 None을 돌려주지 않고 즉시 AssertionError를
    던져야 한다."""
    monkeypatch.setattr(db_schema_conftest, "dotenv_values", lambda _path: {})

    with pytest.raises(AssertionError, match="DATABASE_URL"):
        db_schema_conftest._database_url()
