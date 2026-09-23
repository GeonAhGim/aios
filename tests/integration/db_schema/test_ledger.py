"""3.x — DB 스키마 통합 테스트: LC-6(ledger core)/LC-7(holds/payouts) 절.

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리.
`db_conn`/`raw_conn` fixture는 `conftest.py`에서 자동 제공된다.
Spec: 04_db_schema_v1.7.md.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text

from src.foundation.ledger.contracts.v1 import AccountType
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_PAYOUT_CLEARING,
    PLATFORM_REFUND_RESERVE,
)
from tests.integration.db_schema.conftest import insert_audit_event, insert_ledger_entry

# --- LC-6 (4a1d0c0de005_ledger_core) ---------------------------------------

LEDGER_CORE_TABLES = {
    "ledger_account",
    "ledger_journal_entry",
    "ledger_posting_line",
    "ledger_balance",
    "ledger_control",
}

PLATFORM_HOUSE_USER_ID = "00000000-0000-0000-0000-000000000001"


async def test_ledger_core_tables_exist(db_conn) -> None:
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
async def test_ledger_entry_and_line_worm_revoked_from_public(db_conn, table: str) -> None:
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


async def test_ledger_platform_and_house_accounts_seeded(db_conn) -> None:
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


async def test_ledger_balance_seeded_for_platform_and_house_accounts(db_conn) -> None:
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


async def test_ledger_control_singleton_seeded(db_conn) -> None:
    result = await db_conn.execute(text("SELECT id, write_frozen FROM ledger_control"))
    rows = list(result)
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].write_frozen is False


async def test_unbalanced_entry_fails_at_commit(raw_conn) -> None:
    """§4.4 deferred constraint trigger — Σ차변 != Σ대변인 분개는 개별
    INSERT가 아니라 COMMIT 시점에 실패해야 한다(entry의 모든 행이 다
    들어온 뒤에야 판정 가능하므로)."""
    audit_event_id = await insert_audit_event(raw_conn)
    entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
    accounts = await raw_conn.fetch(
        "SELECT account_id, account_code FROM ledger_account WHERE account_code = ANY($1::text[])",
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


async def test_multi_currency_entry_fails_at_commit(raw_conn) -> None:
    """§4.4 deferred constraint trigger의 두 번째 판정 분기 — 같은 entry에
    서로 다른 통화의 posting line이 섞이면(금액이 맞아떨어져도) COMMIT
    시점에 실패해야 한다. `test_unbalanced_entry_fails_at_commit`은 차대
    불일치만 exercise하고 이 분기는 커버하지 않았다."""
    audit_event_id = await insert_audit_event(raw_conn)
    entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
    accounts = await raw_conn.fetch(
        "SELECT account_id, account_code FROM ledger_account WHERE account_code = ANY($1::text[])",
        [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
    )
    account_id = {row["account_code"]: row["account_id"] for row in accounts}

    with pytest.raises(asyncpg.RaiseError, match="more than one currency"):
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
                "VALUES ($1, 2, $2, 'CREDIT', 100.00, 'USDT')",
                entry_id,
                account_id[PLATFORM_COMMISSION_REVENUE],
            )


async def test_balanced_entry_commits_successfully(raw_conn) -> None:
    """위 테스트의 대조군 — deferred 트리거가 균형 잡힌 분개까지 잘못
    막지 않는지 확인한다.

    task-5687: 여기서 참조하는 `PLATFORM_CASH_CLEARING`/`PLATFORM_COMMISSION_REVENUE`는
    `post_entry`(LC-9) 없이 저널에 실제 커밋되면 `ledger_balance`가 갱신되지
    않아 FA-15 replay_verify가 영구적으로 오탐(false MISMATCH)한다(task-5309와
    동일한 결함 패턴 — TEST_DATABASE_URL은 스위트 실행마다 리셋되지 않는다).
    `test_perf_journal.py`의 `_append_without_persisting`(task-5599)와 같은
    convention으로, 바깥 트랜잭션을 절대 커밋하지 않고 롤백한다 — deferred
    트리거는 `SET CONSTRAINTS ALL IMMEDIATE`로 실제 COMMIT 없이 즉시 평가해
    "균형 잡힌 분개를 트리거가 막지 않는다"는 이 테스트의 주장을 그대로
    검증한다."""
    tx = raw_conn.transaction()
    await tx.start()
    try:
        audit_event_id = await insert_audit_event(raw_conn)
        entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
        accounts = await raw_conn.fetch(
            "SELECT account_id, account_code FROM ledger_account "
            "WHERE account_code = ANY($1::text[])",
            [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
        )
        account_id = {row["account_code"]: row["account_id"] for row in accounts}

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
        await raw_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

        row = await raw_conn.fetchrow(
            "SELECT entry_id FROM ledger_journal_entry WHERE entry_id = $1", entry_id
        )
        assert row is not None
    finally:
        await tx.rollback()


async def test_aios_app_cannot_update_ledger_journal_entry(raw_conn) -> None:
    audit_event_id = await insert_audit_event(raw_conn)
    entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE ledger_journal_entry SET event_ref = 'tampered' WHERE entry_id = $1",
                entry_id,
            )


async def test_aios_app_cannot_delete_ledger_posting_line(raw_conn) -> None:
    """task-5687: `test_balanced_entry_commits_successfully`와 동일한 이유로
    바깥 트랜잭션은 절대 커밋하지 않는다 — 삭제 시도용 posting line도 같은
    바깥 트랜잭션 안에서 같은 세션이 만들었으므로(MVCC 동일 트랜잭션
    가시성) 실제 COMMIT 없이도 DELETE 대상으로 보인다."""
    tx = raw_conn.transaction()
    await tx.start()
    try:
        audit_event_id = await insert_audit_event(raw_conn)
        entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
        accounts = await raw_conn.fetch(
            "SELECT account_id, account_code FROM ledger_account "
            "WHERE account_code = ANY($1::text[])",
            [PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE],
        )
        account_id = {row["account_code"]: row["account_id"] for row in accounts}
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
        await raw_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

        with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
            async with raw_conn.transaction():
                await raw_conn.execute("SET ROLE aios_app")
                await raw_conn.execute(
                    "DELETE FROM ledger_posting_line WHERE entry_id = $1", entry_id
                )
    finally:
        await tx.rollback()


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
    audit_event_id = await insert_audit_event(raw_conn)
    entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
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
    audit_event_id = await insert_audit_event(raw_conn)
    entry_id = await insert_ledger_entry(raw_conn, audit_event_id=audit_event_id)
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
