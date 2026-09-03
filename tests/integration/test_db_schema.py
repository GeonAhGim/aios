"""3.x — 작업트리 섹션 3(DB 스키마) 통합 테스트.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다: `alembic upgrade head`.

Spec: 04_db_schema_v1.7.md, 06_mvp_scope_v1.3.md#§6.3 DoD
("audit_log 테이블에 WORM 제약(REVOKE UPDATE, DELETE) 적용 확인")
"""
from datetime import date, datetime, timedelta, timezone
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
    """balance/held/pending_payout는 시드 시점엔 0이지만, 같은 TEST_DATABASE_URL을
    공유하는 test_post_entry.py/test_backfill.py 등이 이 플랫폼 계정으로 실제
    커밋되는 분개를 내며 값을 바꾼다(전체 스위트 실행 순서에 따라 값이 달라짐) —
    그래서 여기서는 행 존재와 시드 이후 절대 갱신되지 않는 allow_negative만
    순서 독립적으로 확인한다."""
    result = await db_conn.execute(
        text(
            "SELECT b.allow_negative "
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


# --- LB-8 (4a1d0c0de004_positions_journal) ---------------------------------

POSITIONS_JOURNAL_TABLES = {
    "pos_account",
    "pos_journal",
    "pos_snapshot",
    "pos_nav_daily",
}


async def test_positions_journal_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(POSITIONS_JOURNAL_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == POSITIONS_JOURNAL_TABLES


async def _insert_test_user(conn: asyncpg.Connection) -> object:
    return await conn.fetchval(
        "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
        f"test-{uuid4().hex}@example.com",
        "test-hash",
    )


async def _insert_pos_account(conn: asyncpg.Connection, *, tenant_id) -> object:
    return await conn.fetchval(
        "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
        "VALUES ($1, 'BITGET', 'USDT', 'FIFO') RETURNING account_id",
        tenant_id,
    )


async def test_pos_account_duplicate_with_same_connection_rejected(raw_conn):
    """LB-8 DoD — UNIQUE(tenant_id, venue, connection_id) negative(실값):
    같은 (tenant_id, venue, connection_id) 삼중값은 거부되어야 한다."""
    tenant_id = await _insert_test_user(raw_conn)
    connection_id = await raw_conn.fetchval(
        "INSERT INTO account_connection "
        "(tenant_id, owner_subject_id, provider_code, opaque_account_ref, capability_profile) "
        "VALUES ($1, $1, 'bitget', $2, ARRAY[]::text[]) RETURNING id",
        tenant_id,
        f"opaque-{uuid4().hex}",
    )
    await raw_conn.execute(
        "INSERT INTO pos_account (tenant_id, venue, connection_id, base_currency, cost_method) "
        "VALUES ($1, 'BITGET', $2, 'USDT', 'FIFO')",
        tenant_id,
        connection_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_account "
            "(tenant_id, venue, connection_id, base_currency, cost_method) "
            "VALUES ($1, 'BITGET', $2, 'USDT', 'WEIGHTED')",
            tenant_id,
            connection_id,
        )


async def test_pos_account_invalid_cost_method_rejected(raw_conn):
    """LB-8 DoD — cost_method CHECK negative: `CostMethod`(§3.2)에 없는
    값은 DB 레벨에서 거부되어야 한다."""
    tenant_id = await _insert_test_user(raw_conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'BITGET', 'USDT', 'BOGUS_METHOD')",
            tenant_id,
        )


async def _insert_pos_journal_entry(
    conn: asyncpg.Connection, *, tenant_id, account_id, position_key: str, sequence_no: int
) -> None:
    await conn.execute(
        "INSERT INTO pos_journal "
        "(tenant_id, account_id, position_key, sequence_no, entry_type, qty_delta, "
        " source_event_type, source_event_id, idempotency_key, digest, entry_hash, occurred_at) "
        "VALUES ($1, $2, $3, $4, 'FILL', 1.0, 'order', $5, $6, repeat('0', 64), "
        " repeat('0', 64), now())",
        tenant_id,
        account_id,
        position_key,
        sequence_no,
        f"order-{uuid4().hex}",
        f"fill:{uuid4().hex}",
    )


async def test_pos_journal_duplicate_position_key_sequence_no_rejected(raw_conn):
    """LB-8 DoD — UNIQUE(position_key, sequence_no) negative: 같은
    position_key에 같은 sequence_no를 두 번 append하면 거부되어야 한다."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    position_key = f"BITGET:{uuid4().hex}:strat:exec"

    await _insert_pos_journal_entry(
        raw_conn, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        sequence_no=1,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_pos_journal_entry(
            raw_conn, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
            sequence_no=1,
        )


async def test_pos_journal_sequence_no_below_one_rejected(raw_conn):
    """LB-8 DoD — CHECK(sequence_no >= 1) negative."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_pos_journal_entry(
            raw_conn, tenant_id=tenant_id, account_id=account_id,
            position_key=f"BITGET:{uuid4().hex}:strat:exec", sequence_no=0,
        )


async def test_pos_journal_duplicate_idempotency_key_rejected(raw_conn):
    """LB-8 DoD — idempotency_key UNIQUE negative."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    idem_key = f"fill:{uuid4().hex}"

    await raw_conn.execute(
        "INSERT INTO pos_journal "
        "(tenant_id, account_id, position_key, sequence_no, entry_type, qty_delta, "
        " source_event_type, source_event_id, idempotency_key, digest, entry_hash, occurred_at) "
        "VALUES ($1, $2, $3, 1, 'FILL', 1.0, 'order', $4, $5, repeat('0', 64), "
        " repeat('0', 64), now())",
        tenant_id,
        account_id,
        f"BITGET:{uuid4().hex}:strat:exec",
        f"order-{uuid4().hex}",
        idem_key,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_journal "
            "(tenant_id, account_id, position_key, sequence_no, entry_type, qty_delta, "
            " source_event_type, source_event_id, idempotency_key, digest, entry_hash, "
            " occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1.0, 'order', $4, $5, repeat('0', 64), "
            " repeat('0', 64), now())",
            tenant_id,
            account_id,
            f"BITGET:{uuid4().hex}:strat:exec",
            f"order-{uuid4().hex}",
            idem_key,
        )


async def test_aios_app_cannot_update_pos_journal(raw_conn):
    """LB-8 DoD — `pos_journal`은 WORM: `aios_app` 롤로 UPDATE를 시도하면
    append-only 가드 트리거가 막아야 한다(LC-6/LC-7 패턴과 동일)."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    position_key = f"BITGET:{uuid4().hex}:strat:exec"
    await _insert_pos_journal_entry(
        raw_conn, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        sequence_no=1,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE pos_journal SET qty_delta = 2.0 WHERE position_key = $1",
                position_key,
            )


async def test_pos_snapshot_legacy_position_id_fk_enforced(raw_conn):
    """LB-8 DoD — `legacy_position_id` FK `positions(id)` negative: 존재하지
    않는 legacy position id는 거부되어야 한다."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_snapshot "
            "(position_key, tenant_id, account_id, instrument_id, quantity, cost_method, "
            " legacy_position_id) "
            "VALUES ($1, $2, $3, gen_random_uuid(), 1.0, 'FIFO', 999999999)",
            f"BITGET:{uuid4().hex}:strat:exec",
            tenant_id,
            account_id,
        )


async def test_pos_nav_daily_duplicate_account_date_rejected(raw_conn):
    """LB-8 DoD — UNIQUE(account_id, nav_date) negative."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    nav_date = date(2026, 9, 1)

    await raw_conn.execute(
        "INSERT INTO pos_nav_daily "
        "(account_id, nav_date, base_currency, opening_nav, cash, positions_mv, closing_nav, "
        " source_hash) "
        "VALUES ($1, $2, 'USDT', 100.0, 40.0, 60.0, 100.0, repeat('0', 64))",
        account_id,
        nav_date,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_nav_daily "
            "(account_id, nav_date, base_currency, opening_nav, cash, positions_mv, "
            " closing_nav, source_hash) "
            "VALUES ($1, $2, 'USDT', 100.0, 50.0, 50.0, 100.0, repeat('0', 64))",
            account_id,
            nav_date,
        )


async def test_pos_nav_daily_closing_nav_equation_rejected(raw_conn):
    """LB-8 DoD — CHECK(closing_nav = cash + positions_mv) negative."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO pos_nav_daily "
            "(account_id, nav_date, base_currency, opening_nav, cash, positions_mv, "
            " closing_nav, source_hash) "
            "VALUES ($1, '2026-09-01', 'USDT', 100.0, 40.0, 60.0, 999.0, repeat('0', 64))",
            account_id,
        )


async def test_aios_app_cannot_delete_pos_nav_daily(raw_conn):
    """LB-8 DoD — `pos_nav_daily`는 WORM: `aios_app` 롤로 DELETE를 시도하면
    append-only 가드 트리거가 막아야 한다."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    nav_id = await raw_conn.fetchval(
        "INSERT INTO pos_nav_daily "
        "(account_id, nav_date, base_currency, opening_nav, cash, positions_mv, closing_nav, "
        " source_hash) "
        "VALUES ($1, '2026-09-01', 'USDT', 100.0, 40.0, 60.0, 100.0, repeat('0', 64)) "
        "RETURNING nav_id",
        account_id,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute("DELETE FROM pos_nav_daily WHERE nav_id = $1", nav_id)


# --- LA-10 (4a1d0c0de007_md_reference_registry) ----------------------------

MD_REFERENCE_REGISTRY_TABLES = {
    "md_instrument",
    "md_symbol_alias",
    "md_corporate_action",
    "md_venue_calendar_day",
}


async def test_md_reference_registry_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(MD_REFERENCE_REGISTRY_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == MD_REFERENCE_REGISTRY_TABLES


async def _insert_md_instrument(conn: asyncpg.Connection, *, canonical_symbol: str) -> object:
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        canonical_symbol,
    )


async def test_md_instrument_invalid_status_rejected(raw_conn):
    """LA-10 DoD — status CHECK negative: `SymbolStatus`(§3.1)에 없는 값은
    DB 레벨에서 거부되어야 한다."""
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
            " status, listed_at) "
            "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'BOGUS_STATUS', now())",
            f"TEST-{uuid4().hex}",
        )


async def test_md_symbol_alias_overlapping_period_rejected(raw_conn):
    """LA-10 DoD — `EXCLUDE USING gist` negative(btree_gist): 같은
    (venue, alias_symbol)의 유효기간이 겹치면 두 번째 별칭 insert가
    거부되어야 한다."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    alias_symbol = f"ALIAS-{uuid4().hex}"
    valid_from = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await raw_conn.execute(
        "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
        "VALUES ($1, 'BITGET', $2, $3)",
        instrument_id,
        alias_symbol,
        valid_from,
    )
    with pytest.raises(asyncpg.ExclusionViolationError):
        await raw_conn.execute(
            "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
            "VALUES ($1, 'BITGET', $2, $3)",
            instrument_id,
            alias_symbol,
            valid_from + timedelta(days=1),
        )


async def test_md_corporate_action_non_positive_ratio_rejected(raw_conn):
    """LA-10 DoD — CHECK(ratio > 0) negative."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_corporate_action "
            "(instrument_id, action_type, ex_date, ratio, source_ref) "
            "VALUES ($1, 'SPLIT', '2026-06-01', 0, 'test')",
            instrument_id,
        )


async def test_md_venue_calendar_day_trading_flag_mismatch_rejected(raw_conn):
    """LA-10 DoD — CHECK(is_trading_day = (open_at IS NOT NULL)) negative."""
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_venue_calendar_day "
            "(venue, trade_date, is_trading_day, open_at, source) "
            "VALUES ('BITGET', '2026-06-01', true, NULL, 'test')"
        )


# --- LA-11 (4a1d0c0de008_md_candles) ---------------------------------------

MD_CANDLES_TABLES = {
    "md_candle",
    "md_quarantine_candle",
    "md_tick",
    "md_ingest_batch",
    "md_quality_issue",
}


async def test_md_candles_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(MD_CANDLES_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == MD_CANDLES_TABLES


async def _insert_md_ingest_batch(conn: asyncpg.Connection, *, instrument_id) -> object:
    audit_event_id = await _insert_audit_event(conn)
    return await conn.fetchval(
        "INSERT INTO md_ingest_batch "
        "(source, venue, instrument_id, timeframe, range_start, range_end, "
        " request_fingerprint, batch_hash, verdict, audit_event_id) "
        "VALUES ('test', 'BITGET', $1, '1m', now(), now(), $2, $3, 'ACCEPT', $4) "
        "RETURNING id",
        instrument_id,
        f"fp-{uuid4().hex}",
        f"hash-{uuid4().hex}",
        audit_event_id,
    )


async def _md_candles_setup(conn: asyncpg.Connection) -> tuple[object, object]:
    instrument_id = await _insert_md_instrument(conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(conn, instrument_id=instrument_id)
    return instrument_id, batch_id


async def _insert_md_candle(
    conn: asyncpg.Connection,
    *,
    instrument_id,
    batch_id,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    open_time: datetime | None = None,
) -> None:
    open_time = open_time or datetime.now(timezone.utc)
    close_time = open_time + timedelta(minutes=1)
    await conn.execute(
        "INSERT INTO md_candle "
        "(venue, instrument_id, timeframe, open_time, close_time, "
        " open, high, low, close, volume, batch_id) "
        "VALUES ('BITGET', $1, '1m', $2, $3, $4, $5, $6, $7, $8, $9)",
        instrument_id,
        open_time,
        close_time,
        open_,
        high,
        low,
        close,
        volume,
        batch_id,
    )


@pytest.mark.parametrize(
    "check_name,ohlcv",
    [
        ("ck_md_candle_high_ge_open", (100, 90, 80, 85, 10)),
        ("ck_md_candle_high_ge_close", (80, 90, 70, 100, 10)),
        ("ck_md_candle_high_ge_low", (50, 60, 70, 50, 10)),
        ("ck_md_candle_low_le_open", (50, 80, 60, 70, 10)),
        ("ck_md_candle_low_le_close", (80, 90, 70, 60, 10)),
        ("ck_md_candle_volume_nonneg", (100, 110, 90, 105, -1)),
    ],
)
async def test_md_candle_ohlcv_check_violations_rejected(raw_conn, check_name, ohlcv):
    """LA-11 DoD — §4.1 CHECK 6종 negative(실값): 각 부등식을 하나씩만
    위반하는 (open, high, low, close, volume) 조합으로 INSERT가 거부되는지
    증명한다(스킵 금지). 값 조합은 실제 asyncpg 세션으로 사전 검증됨
    (task-450 note) — 각 케이스는 해당 CHECK가 다른 5종보다 먼저 평가되도록
    골랐다."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    open_, high, low, close, volume = ohlcv
    with pytest.raises(asyncpg.CheckViolationError, match=check_name):
        await _insert_md_candle(
            raw_conn,
            instrument_id=instrument_id,
            batch_id=batch_id,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )


async def test_md_candle_valid_ohlcv_accepted(raw_conn):
    """위 6종 CHECK 테스트의 대조군 — 정상 캔들까지 잘못 막지 않는지 확인."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    await _insert_md_candle(
        raw_conn, instrument_id=instrument_id, batch_id=batch_id,
        open_=100, high=110, low=90, close=105, volume=10,
    )
    row = await raw_conn.fetchrow(
        "SELECT open FROM md_candle WHERE instrument_id = $1", instrument_id
    )
    assert row is not None


async def test_aios_app_cannot_update_md_candle(raw_conn):
    """LA-11 DoD — `md_candle`은 WORM: 파티션 부모에 건 append-only 가드가
    (지금 달 파티션이 아니라) 미래 달 파티션에 저장된 행에도 적용되는지까지
    함께 증명한다(PG11+ 트리거 클로닝, 마이그레이션 docstring 참조)."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    future_open_time = datetime.now(timezone.utc) + timedelta(days=90)
    # 마이그레이션 upgrade()가 만든 파티션은 +3개월까지뿐이라 90일 뒤가 그
    # 경계를 넘을 수 있다(월별 경계는 날짜와 무관) — 먼저 여유 있게 확장한다.
    await raw_conn.execute("SELECT md_ensure_partitions(6)")
    await _insert_md_candle(
        raw_conn, instrument_id=instrument_id, batch_id=batch_id,
        open_=100, high=110, low=90, close=105, volume=10,
        open_time=future_open_time,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE md_candle SET volume = 999 WHERE instrument_id = $1", instrument_id
            )


async def test_aios_app_cannot_delete_md_ingest_batch(raw_conn):
    """LA-11 DoD — `md_ingest_batch`도 WORM 대상(명세 §9.2 LA-11 표)."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(raw_conn, instrument_id=instrument_id)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute("DELETE FROM md_ingest_batch WHERE id = $1", batch_id)


async def test_aios_app_cannot_update_md_quality_issue(raw_conn):
    """LA-11 DoD — `md_quality_issue`도 WORM 대상."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(raw_conn, instrument_id=instrument_id)
    issue_id = await raw_conn.fetchval(
        "INSERT INTO md_quality_issue (batch_id, type, severity, detail) "
        "VALUES ($1, 'GAP', 'WARN', '{}'::jsonb) RETURNING id",
        batch_id,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE md_quality_issue SET severity = 'REJECT' WHERE id = $1", issue_id
            )


async def test_md_ensure_partitions_creates_future_partitions(raw_conn):
    """LA-11 DoD — `md_ensure_partitions(months_ahead)` 호출이 실제로 새
    파티션을 만드는지 확인한다: 마이그레이션이 이미 만들어 둔 범위(3개월)를
    넘어서는 달의 파티션을 요청해 그 전에는 없었다가 호출 후 생겼는지
    증명한다. `aios_app`으로 호출해 SECURITY DEFINER가 실제로 필요한지도
    함께 검증한다(런타임 role은 스키마 CREATE 권한이 없다)."""
    before = await raw_conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'md_candle'::regclass"
    )
    before_names = {row["relname"] for row in before}
    # 같은 TEST_DATABASE_URL을 다른 테스트(위 test_aios_app_cannot_update_md_candle
    # 등)와 공유해 이미 몇 달치가 만들어져 있을 수 있다 — 지금 있는 것보다
    # 확실히 더 먼 미래를 요청해야 "새로 생겼다"는 판정이 순서 독립적이다.
    months_ahead = len(before_names) + 2

    async with raw_conn.transaction():
        await raw_conn.execute("SET ROLE aios_app")
        await raw_conn.execute("SELECT md_ensure_partitions($1)", months_ahead)

    after = await raw_conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'md_candle'::regclass"
    )
    after_names = {row["relname"] for row in after}
    assert after_names - before_names, "md_ensure_partitions()가 새 파티션을 만들지 않았다"
