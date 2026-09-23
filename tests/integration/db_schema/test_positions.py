"""3.x — DB 스키마 통합 테스트: LB-8(positions journal) 절.

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리.
`db_conn`/`raw_conn` fixture는 `conftest.py`에서 자동 제공된다.
Spec: 04_db_schema_v1.7.md.
"""

from datetime import date
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text

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
    """Create a `users` row plus its PERSONAL `tenant` row (id == user_id).

    `account_connection`/`pos_account`/... all FK `tenant_id` to `tenant(id)`
    (FA-0a) rather than `users(user_id)`, so a bare `users` row is not enough
    for any INSERT that carries a real (non-NULL) `tenant_id`.
    """
    user_id = await conn.fetchval(
        "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
        f"test-{uuid4().hex}@example.com",
        "test-hash",
    )
    await conn.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'PERSONAL')", user_id)
    return user_id


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
        raw_conn,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        sequence_no=1,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_pos_journal_entry(
            raw_conn,
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            sequence_no=1,
        )


async def test_pos_journal_sequence_no_below_one_rejected(raw_conn):
    """LB-8 DoD — CHECK(sequence_no >= 1) negative."""
    tenant_id = await _insert_test_user(raw_conn)
    account_id = await _insert_pos_account(raw_conn, tenant_id=tenant_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_pos_journal_entry(
            raw_conn,
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=f"BITGET:{uuid4().hex}:strat:exec",
            sequence_no=0,
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
        raw_conn,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
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
