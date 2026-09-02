"""3.x — 작업트리 섹션 3(DB 스키마) 통합 테스트.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다: `alembic upgrade head`.

Spec: 04_db_schema_v1.7.md, 06_mvp_scope_v1.3.md#§6.3 DoD
("audit_log 테이블에 WORM 제약(REVOKE UPDATE, DELETE) 적용 확인")
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.foundation.ledger.contracts.v1 import AccountType
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_PAYOUT_CLEARING,
    PLATFORM_REFUND_RESERVE,
)

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


@pytest.fixture
async def raw_conn():
    """LC-6 트랜잭션·롤 테스트용 — deferred 트리거의 커밋 시점 동작과
    `SET ROLE`은 asyncpg 원시 커넥션(`test_db_roles.py`와 동일 패턴)으로만
    직접 검증할 수 있다(SQLAlchemy `AsyncConnection`은 커밋 시점을 감춘다)."""
    dsn = _database_url().replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    yield connection
    await connection.close()


async def _insert_audit_event(conn: asyncpg.Connection):
    row = await conn.fetchrow(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.ledger', gen_random_uuid(), 'test.ledger.post', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid4().int % (2**62),  # system(tenant_id IS NULL) sequence_no 유일 제약 회피용 난수
    )
    return row["id"]


async def _insert_entry(conn: asyncpg.Connection, *, audit_event_id) -> object:
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


# --- LC-6 (4a1d0c0de005_ledger_core) ---------------------------------------

LEDGER_CORE_TABLES = {
    "ledger_account",
    "ledger_journal_entry",
    "ledger_posting_line",
    "ledger_balance",
    "ledger_control",
}

PLATFORM_HOUSE_USER_ID = "00000000-0000-0000-0000-000000000001"


async def test_ledger_core_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(LEDGER_CORE_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == LEDGER_CORE_TABLES


@pytest.mark.parametrize("table", ["ledger_journal_entry", "ledger_posting_line"])
async def test_ledger_entry_and_line_worm_revoked_from_public(db_conn, table):
    result = await db_conn.execute(
        text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = :table AND grantee = 'PUBLIC'"
        ),
        {"table": table},
    )
    granted = {row[0] for row in result}
    assert "UPDATE" not in granted
    assert "DELETE" not in granted


async def test_ledger_platform_and_house_accounts_seeded(db_conn):
    """계정코드·유형이 `domain/chart_of_accounts.py`(LC-2)의 상수와 어긋나면
    LC-9(post_entry)가 계정을 못 찾거나 잘못된 부호로 분개한다 — 마이그레이션
    시드값이 도메인 모듈과 같은 값인지 여기서 고정한다."""
    expected = {
        PLATFORM_CASH_CLEARING: AccountType.ASSET.value,
        PLATFORM_COMMISSION_REVENUE: AccountType.REVENUE.value,
        PLATFORM_REFUND_RESERVE: AccountType.EXPENSE.value,
        PLATFORM_PAYOUT_CLEARING: AccountType.CLEARING.value,
        f"USER:{PLATFORM_HOUSE_USER_ID}:AVAILABLE": AccountType.LIABILITY.value,
    }
    result = await db_conn.execute(
        text(
            "SELECT account_code, account_type, currency, allow_negative FROM ledger_account "
            "WHERE account_code = ANY(:codes)"
        ),
        {"codes": list(expected)},
    )
    rows = {row[0]: row for row in result}
    assert set(rows) == set(expected)
    for code, expected_type in expected.items():
        row = rows[code]
        assert row.account_type == expected_type, code
        assert row.currency == "KRW", code
        assert row.allow_negative is False, code


async def test_ledger_balance_seeded_for_platform_and_house_accounts(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT b.balance, b.held, b.pending_payout, b.allow_negative "
            "FROM ledger_balance b JOIN ledger_account a ON a.account_id = b.account_id "
            "WHERE a.account_code = ANY(:codes)"
        ),
        {
            "codes": [
                PLATFORM_CASH_CLEARING,
                PLATFORM_COMMISSION_REVENUE,
                PLATFORM_REFUND_RESERVE,
                PLATFORM_PAYOUT_CLEARING,
                f"USER:{PLATFORM_HOUSE_USER_ID}:AVAILABLE",
            ]
        },
    )
    rows = list(result)
    assert len(rows) == 5
    for row in rows:
        assert row.balance == 0
        assert row.held == 0
        assert row.pending_payout == 0
        assert row.allow_negative is False


async def test_ledger_control_singleton_seeded(db_conn):
    result = await db_conn.execute(
        text("SELECT id, write_frozen FROM ledger_control")
    )
    rows = list(result)
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].write_frozen is False


async def test_unbalanced_entry_fails_at_commit(raw_conn):
    """§4.4 deferred constraint trigger — Σ차변 != Σ대변인 분개는 개별
    INSERT가 아니라 COMMIT 시점에 실패해야 한다(entry의 모든 행이 다
    들어온 뒤에야 판정 가능하므로)."""
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)
    accounts = await raw_conn.fetch(
        "SELECT account_id, account_code FROM ledger_account "
        "WHERE account_code = ANY($1::text[])",
        [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
    )
    account_id = {row["account_code"]: row["account_id"] for row in accounts}

    with pytest.raises(asyncpg.RaiseError, match="unbalanced"):
        async with raw_conn.transaction():
            await raw_conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency) "
                "VALUES ($1, 1, $2, 'DEBIT', 100.00, 'KRW')",
                entry_id,
                account_id[PLATFORM_CASH_CLEARING],
            )
            await raw_conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency) "
                "VALUES ($1, 2, $2, 'CREDIT', 99.00, 'KRW')",
                entry_id,
                account_id[PLATFORM_COMMISSION_REVENUE],
            )


async def test_balanced_entry_commits_successfully(raw_conn):
    """위 테스트의 대조군 — deferred 트리거가 균형 잡힌 분개까지 잘못
    막지 않는지 확인한다."""
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)
    accounts = await raw_conn.fetch(
        "SELECT account_id, account_code FROM ledger_account "
        "WHERE account_code = ANY($1::text[])",
        [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
    )
    account_id = {row["account_code"]: row["account_id"] for row in accounts}

    async with raw_conn.transaction():
        await raw_conn.execute(
            "INSERT INTO ledger_posting_line "
            "(entry_id, line_no, account_id, side, amount, currency) "
            "VALUES ($1, 1, $2, 'DEBIT', 100.00, 'KRW')",
            entry_id,
            account_id[PLATFORM_CASH_CLEARING],
        )
        await raw_conn.execute(
            "INSERT INTO ledger_posting_line "
            "(entry_id, line_no, account_id, side, amount, currency) "
            "VALUES ($1, 2, $2, 'CREDIT', 100.00, 'KRW')",
            entry_id,
            account_id[PLATFORM_COMMISSION_REVENUE],
        )

    row = await raw_conn.fetchrow(
        "SELECT entry_id FROM ledger_journal_entry WHERE entry_id = $1", entry_id
    )
    assert row is not None


async def test_aios_app_cannot_update_ledger_journal_entry(raw_conn):
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE ledger_journal_entry SET event_ref = 'tampered' WHERE entry_id = $1",
                entry_id,
            )


async def test_aios_app_cannot_delete_ledger_posting_line(raw_conn):
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)
    accounts = await raw_conn.fetch(
        "SELECT account_id, account_code FROM ledger_account "
        "WHERE account_code = ANY($1::text[])",
        [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
    )
    account_id = {row["account_code"]: row["account_id"] for row in accounts}
    async with raw_conn.transaction():
        await raw_conn.execute(
            "INSERT INTO ledger_posting_line "
            "(entry_id, line_no, account_id, side, amount, currency) "
            "VALUES ($1, 1, $2, 'DEBIT', 100.00, 'KRW')",
            entry_id,
            account_id[PLATFORM_CASH_CLEARING],
        )
        await raw_conn.execute(
            "INSERT INTO ledger_posting_line "
            "(entry_id, line_no, account_id, side, amount, currency) "
            "VALUES ($1, 2, $2, 'CREDIT', 100.00, 'KRW')",
            entry_id,
            account_id[PLATFORM_COMMISSION_REVENUE],
        )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "DELETE FROM ledger_posting_line WHERE entry_id = $1", entry_id
            )


# --- LC-7 (4a1d0c0de006_ledger_holds_payouts) ------------------------------

LEDGER_HOLDS_PAYOUTS_TABLES = {
    "ledger_hold",
    "ledger_payout_batch",
    "ledger_payout_item",
    "ledger_integrity_check",
}


async def test_ledger_holds_payouts_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(LEDGER_HOLDS_PAYOUTS_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == LEDGER_HOLDS_PAYOUTS_TABLES


async def _cash_clearing_account_id(conn: asyncpg.Connection):
    return await conn.fetchval(
        "SELECT account_id FROM ledger_account WHERE account_code = $1",
        PLATFORM_CASH_CLEARING,
    )


async def test_ledger_hold_duplicate_purpose_reference_rejected(raw_conn):
    """LC-7 DoD — UNIQUE(purpose, reference) negative: 같은 (purpose, reference)
    쌍은 두 번째 홀드 생성 시도를 막아야 한다(이중 홀드 방지)."""
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)
    account_id = await _cash_clearing_account_id(raw_conn)
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    purpose = f"test-purpose-{uuid4().hex}"
    reference = f"test-ref-{uuid4().hex}"

    await raw_conn.execute(
        "INSERT INTO ledger_hold "
        "(account_id, amount, currency, purpose, reference, state, expires_at, entry_id) "
        "VALUES ($1, 100.00, 'KRW', $2, $3, 'PENDING', $4, $5)",
        account_id,
        purpose,
        reference,
        expires_at,
        entry_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_conn.execute(
            "INSERT INTO ledger_hold "
            "(account_id, amount, currency, purpose, reference, state, expires_at, entry_id) "
            "VALUES ($1, 50.00, 'KRW', $2, $3, 'PENDING', $4, $5)",
            account_id,
            purpose,
            reference,
            expires_at,
            entry_id,
        )


async def test_ledger_hold_invalid_state_rejected(raw_conn):
    """LC-7 DoD — state CHECK negative: `HoldState`(§4.5)에 없는 값은 DB
    레벨에서 거부되어야 한다(도메인 검증 우회 시 최후 방어선)."""
    audit_event_id = await _insert_audit_event(raw_conn)
    entry_id = await _insert_entry(raw_conn, audit_event_id=audit_event_id)
    account_id = await _cash_clearing_account_id(raw_conn)
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)

    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO ledger_hold "
            "(account_id, amount, currency, purpose, reference, state, expires_at, entry_id) "
            "VALUES ($1, 100.00, 'KRW', $2, $3, 'BOGUS_STATE', $4, $5)",
            account_id,
            f"test-purpose-{uuid4().hex}",
            f"test-ref-{uuid4().hex}",
            expires_at,
            entry_id,
        )


async def test_aios_app_cannot_update_ledger_integrity_check(raw_conn):
    """LC-7 DoD — `ledger_integrity_check`는 WORM: `aios_app` 롤로 UPDATE를
    시도하면 append-only 가드 트리거가 막아야 한다(LC-6 패턴과 동일)."""
    check_id = await raw_conn.fetchval(
        "INSERT INTO ledger_integrity_check (result, report) "
        "VALUES ('OK', '{}'::jsonb) RETURNING check_id"
    )
    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE ledger_integrity_check SET result = 'DRIFT' WHERE check_id = $1",
                check_id,
            )
