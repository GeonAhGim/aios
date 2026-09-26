"""FA-4(963d5f3cfb1b) 마이그레이션 — pos_journal WORM 테이블(백필 없음) 회귀.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4 DoD.
decision(task-1794): `pos_journal`·`ledger_journal_entry`·`ledger_posting_line`은
이미 append-only WORM 트리거가 걸려 있어(`4a1d0c0de004`/`4a1d0c0de005`) 물리적으로
재기록 불가하다 — 컬럼/FK/인덱스만 추가하고 백필은 하지 않는다(영구 NULL).
`pos_journal`은 FA-3 `fills` 테스트와 동일 패턴으로 "부트스트랩된 tenant라도
백필되지 않음"을 증명한다(eligible한 데이터가 있어도 WORM이라 스킵됨을
분명히 하기 위해). `ledger_journal_entry`/`ledger_posting_line`의 동일 회귀 +
원장 대차 불변/실패주입 회귀는 `test_migration_fa4_worm_ledger_journal.py`에
있다(이 파일과 함께 5xx줄 file-policy 관측선을 넘지 않도록 분리, ADR-2026-09-10-C
§7)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "789c138f13fe"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


def _sweep_synthetic_snapshots(prefix: str) -> None:
    """FA-0d-fix (task-771991202): rows this module inserts below FA-4 carry
    synthetic non-5-part keys that `cdb114b6903f` (FA-0d) refuses fail-closed,
    so they are removed before the schema is brought back to head."""
    import asyncio

    async def _sweep() -> None:
        conn = await asyncpg.connect(_asyncpg_dsn())
        try:
            await conn.execute("DELETE FROM pos_snapshot WHERE position_key LIKE $1", f"{prefix}%")
        finally:
            await conn.close()

    asyncio.run(_sweep())


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _sweep_synthetic_snapshots("fa4-worm-test-")
    _run_alembic("upgrade", "head")


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
