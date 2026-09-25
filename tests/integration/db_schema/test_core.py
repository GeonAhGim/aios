"""3.x — DB 스키마 통합 테스트: 섹션 3 기본 테이블/WORM/다자산군 컬럼.

Spec: 04_db_schema_v1.7.md, 06_mvp_scope_v1.3.md#§6.3 DoD
("audit_log 테이블에 WORM 제약(REVOKE UPDATE, DELETE) 적용 확인")

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리. `db_conn`
fixture는 `conftest.py`에서 자동 제공된다.
"""

from sqlalchemy import text

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
