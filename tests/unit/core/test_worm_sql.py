"""L0-3: WORM 롤/트리거 SQL 생성기 스냅샷 + 인젝션 거부.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-3
DoD: 생성 SQL 스냅샷 일치, 테이블/역할명에 `;` 등 인젝션성 식별자는 거부.
"""
import pytest

from src.core.db.append_only import InvalidIdentifierError as AppendOnlyIdentifierError
from src.core.db.append_only import worm_drop_sql, worm_sql
from src.core.db.roles import InvalidIdentifierError as RolesIdentifierError
from src.core.db.roles import ensure_roles_sql


def test_worm_sql_snapshot():
    assert worm_sql("pos_journal") == [
        "REVOKE UPDATE, DELETE ON pos_journal FROM PUBLIC",
        (
            "CREATE OR REPLACE FUNCTION pos_journal_worm_guard() RETURNS trigger AS $$\n"
            "BEGIN\n"
            "    RAISE EXCEPTION 'append-only violation: % on pos_journal denied', TG_OP;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql"
        ),
        (
            "CREATE TRIGGER pos_journal_worm_guard_trg\n"
            "    BEFORE UPDATE OR DELETE ON pos_journal\n"
            "    FOR EACH ROW EXECUTE FUNCTION pos_journal_worm_guard()"
        ),
    ]


def test_worm_drop_sql_snapshot():
    assert worm_drop_sql("pos_journal") == [
        "DROP TRIGGER IF EXISTS pos_journal_worm_guard_trg ON pos_journal",
        "DROP FUNCTION IF EXISTS pos_journal_worm_guard()",
        "GRANT UPDATE, DELETE ON pos_journal TO PUBLIC",
    ]


@pytest.mark.parametrize(
    "table",
    [
        "pos_journal; DROP TABLE users--",
        "pos_journal;",
        "pos journal",
        "pos-journal",
        "'; DROP TABLE users; --",
        "",
        "123_table",
    ],
)
def test_worm_sql_rejects_injection_table_names(table):
    with pytest.raises(AppendOnlyIdentifierError):
        worm_sql(table)
    with pytest.raises(AppendOnlyIdentifierError):
        worm_drop_sql(table)


def test_ensure_roles_sql_snapshot():
    assert ensure_roles_sql(app_role="aios_app", migrator_role="aios_migrator") == [
        (
            "DO $$\n"
            "BEGIN\n"
            "    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = "
            "'aios_migrator') THEN\n"
            "        CREATE ROLE aios_migrator LOGIN;\n"
            "    END IF;\n"
            "END\n"
            "$$"
        ),
        (
            "DO $$\n"
            "BEGIN\n"
            "    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = "
            "'aios_app') THEN\n"
            "        CREATE ROLE aios_app LOGIN;\n"
            "    END IF;\n"
            "END\n"
            "$$"
        ),
        "GRANT ALL PRIVILEGES ON SCHEMA public TO aios_migrator",
        "GRANT USAGE ON SCHEMA public TO aios_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aios_app",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aios_app",
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE aios_migrator IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aios_app"
        ),
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE aios_migrator IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO aios_app"
        ),
    ]


@pytest.mark.parametrize(
    "app_role,migrator_role",
    [
        ("aios_app; DROP TABLE users--", "aios_migrator"),
        ("aios_app", "aios_migrator; DROP TABLE users--"),
        ("aios app", "aios_migrator"),
        ("", "aios_migrator"),
    ],
)
def test_ensure_roles_sql_rejects_injection_role_names(app_role, migrator_role):
    with pytest.raises(RolesIdentifierError):
        ensure_roles_sql(app_role=app_role, migrator_role=migrator_role)


def test_ensure_roles_sql_rejects_same_role_for_both():
    with pytest.raises(RolesIdentifierError):
        ensure_roles_sql(app_role="aios_app", migrator_role="aios_app")
