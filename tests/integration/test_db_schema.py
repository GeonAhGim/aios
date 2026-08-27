"""3.x — 작업트리 섹션 3(DB 스키마) 통합 테스트.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다: `alembic upgrade head`.

Spec: 04_db_schema_v1.7.md, 06_mvp_scope_v1.3.md#§6.3 DoD
("audit_log 테이블에 WORM 제약(REVOKE UPDATE, DELETE) 적용 확인")
"""
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

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
}


def _database_url() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
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
