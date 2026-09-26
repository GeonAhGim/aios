"""FA-4(963d5f3cfb1b) 마이그레이션 — WORM 테이블(백필 없음) + guard UPDATE/DELETE 거부 회귀.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4 DoD.
decision(task-1794): `pos_journal`·`ledger_journal_entry`·`ledger_posting_line`은
이미 append-only WORM 트리거가 걸려 있어(`4a1d0c0de004`/`4a1d0c0de005`) 물리적으로
재기록 불가하다 — 컬럼/FK/인덱스만 추가하고 백필은 하지 않는다(영구 NULL).
`pos_journal`은 FA-3 `fills` 테스트와 동일 패턴으로 "부트스트랩된 tenant라도
백필되지 않음"을 증명한다(eligible한 데이터가 있어도 WORM이라 스킵됨을
분명히 하기 위해). `ledger_journal_entry`/`ledger_posting_line`은 tenant_id
컬럼 자체가 없어 이 마이그레이션의 백필 대상 목록에 애초에 들어가지 않는다
— 컬럼 추가 후에도 NULL로 남는지만 확인한다.

balance invariant 회귀·실패주입·롤백 테스트는
`test_migration_fa4_worm_failure_injection.py`로 분리했다(task-7810,
782e05f9/task-7707가 이 파일을 524줄로 키운 것을 관심사별로 재분할).
공용 픽스처/헬퍼는 `_fa4_worm_support.py`에 있다."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities._fa4_worm_support import (
    _DOWN_REVISION,
    _ensure_head,  # noqa: F401 -- re-exported autouse fixture
    _insert_pre_fa4_ledger_entry,
    _run_alembic,
    pool,  # noqa: F401 -- re-exported fixture
)
from tests.support.deep_downgrade import purge_position_snapshots

__all__ = ["pool", "_ensure_head"]


async def test_pos_journal_never_backfilled_because_worm_blocks_update(pool):
    tenant_id = await create_test_tenant(pool)

    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            tenant_id,
        )
        position_key = f"fa4-worm-test-{uuid4().hex}"
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            position_key,
            tenant_id,
            account_id,
            uuid4(),
        )
        journal_id = await conn.fetchval(
            "INSERT INTO pos_journal (tenant_id, account_id, position_key, sequence_no, "
            "entry_type, qty_delta, source_event_type, source_event_id, idempotency_key, "
            "digest, entry_hash, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa4-worm-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa4-worm-test-{uuid4().hex}",
        )

    _run_alembic("upgrade", "963d5f3cfb1b")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_journal WHERE id = $1", journal_id
        )
        backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE id = $1 AND fund_id IS NOT NULL", journal_id
        )
        null_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE id = $1 AND fund_id IS NULL", journal_id
        )

    assert row["fund_id"] is None
    assert row["portfolio_id"] is None
    assert backfilled == 0
    assert null_count == 1


async def test_pos_journal_worm_guard_rejects_update(pool):
    """negative: `963d5f3cfb1b`이 새로 추가한 `fund_id`/`portfolio_id`
    컬럼조차도 기존 `pos_journal_worm_guard_trg`(4a1d0c0de004)를 우회하지
    못한다 — 이 마이그레이션이 WORM 가드를 약화시키지 않았음을 직접
    UPDATE 시도로 확인한다."""
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            tenant_id,
        )
        position_key = f"fa4-worm-test-{uuid4().hex}"
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            position_key,
            tenant_id,
            account_id,
            uuid4(),
        )
        journal_id = await conn.fetchval(
            "INSERT INTO pos_journal (tenant_id, account_id, position_key, sequence_no, "
            "entry_type, qty_delta, source_event_type, source_event_id, idempotency_key, "
            "digest, entry_hash, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa4-worm-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa4-worm-test-{uuid4().hex}",
        )

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE pos_journal SET fund_id = NULL WHERE id = $1", journal_id)


async def test_pos_journal_worm_guard_rejects_delete(pool):
    """negative: 같은 가드가 DELETE도 거부하는지 확인한다 — UPDATE만
    막고 DELETE로 우회할 수 있다면 append-only 보장이 무의미하다."""
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            tenant_id,
        )
        position_key = f"fa4-worm-test-{uuid4().hex}"
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            position_key,
            tenant_id,
            account_id,
            uuid4(),
        )
        journal_id = await conn.fetchval(
            "INSERT INTO pos_journal (tenant_id, account_id, position_key, sequence_no, "
            "entry_type, qty_delta, source_event_type, source_event_id, idempotency_key, "
            "digest, entry_hash, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa4-worm-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa4-worm-test-{uuid4().hex}",
        )

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM pos_journal WHERE id = $1", journal_id)

    async with pool.acquire() as conn:
        still_there = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE id = $1", journal_id
        )
    assert still_there == 1


async def test_ledger_journal_entry_and_posting_line_never_backfilled(pool):
    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION)
    entry_id = await _insert_pre_fa4_ledger_entry(pool, f"fa4-worm-test:{uuid4().hex}", uuid4())

    _run_alembic("upgrade", "head")

    async with pool.acquire() as conn:
        entry_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM ledger_journal_entry WHERE entry_id = $1",
            entry_id,
        )
        line_rows = await conn.fetch(
            "SELECT fund_id, portfolio_id FROM ledger_posting_line WHERE entry_id = $1",
            entry_id,
        )
        entry_backfilled = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE entry_id = $1 AND fund_id IS NOT NULL",
            entry_id,
        )
        line_backfilled = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line WHERE entry_id = $1 AND fund_id IS NOT NULL",
            entry_id,
        )
        line_null = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line WHERE entry_id = $1 AND fund_id IS NULL",
            entry_id,
        )

    assert entry_row["fund_id"] is None
    assert entry_row["portfolio_id"] is None
    assert entry_backfilled == 0
    assert len(line_rows) == 2
    assert all(row["fund_id"] is None and row["portfolio_id"] is None for row in line_rows)
    assert line_backfilled == 0
    assert line_null == 2
