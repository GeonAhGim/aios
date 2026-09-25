"""FA-0d(cdb114b6903f) 마이그레이션 실DB 백필/역산-불가 실패 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(§9 표 113행). task-1943.

subprocess로 alembic을 띄우는 이유·DSN 해석은 FA-4
(`test_migration_fa4_columns.py`)와 동일 패턴을 따른다.

두 가지를 실DB로 확인한다:
1. `portfolio_id`가 이미 채워진(FA-4 백필 완료) `pos_snapshot` 행은
   `position_key`가 4부분→5부분으로 재작성되고, 같은 `position_key`를 가리키던
   `pos_journal` 행은(WORM) 옛 4부분 형식 그대로 남는다(마이그레이션
   docstring이 밝히는 알려진 한계 — 이 리프가 새로 만드는 결함이 아니라
   WORM이 강제하는 트레이드오프임을 회귀로 고정한다).
2. `portfolio_id`가 NULL인(FA-4 백필 미완료) 행이 하나라도 있으면
   마이그레이션 전체가 실패하고(트랜잭션 전체 롤백 — 같은 upgrade 호출 안의
   백필 가능한 다른 행도 함께 롤백됨), `pos_snapshot`은 옛 형식 그대로
   남는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position
from tests.support.entities_seed import bootstrap_default_portfolio

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "18965d657219"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )


def _run_alembic_ok(*args: str) -> None:
    result = _run_alembic(*args)
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic_ok("upgrade", "head")
    yield
    _run_alembic_ok("upgrade", "head")


async def _insert_pos_account(pool: asyncpg.Pool, tenant_id) -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            tenant_id,
        )


async def _insert_legacy_pos_snapshot(
    pool: asyncpg.Pool, *, position_key: str, tenant_id, account_id, portfolio_id
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method, portfolio_id) VALUES ($1, $2, $3, $4, 0, 'FIFO', $5)",
            position_key,
            tenant_id,
            account_id,
            uuid4(),
            portfolio_id,
        )


async def _insert_legacy_pos_journal(
    pool: asyncpg.Pool, *, position_key: str, tenant_id, account_id
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO pos_journal (tenant_id, account_id, position_key, sequence_no, "
            "entry_type, qty_delta, source_event_type, source_event_id, idempotency_key, "
            "digest, entry_hash, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa0d-migration-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa0d-migration-test-{uuid4().hex}",
        )


async def test_backfill_rewrites_snapshot_key_but_leaves_worm_journal_untouched(pool):
    tenant_id = await create_test_tenant(pool)
    portfolio_id = await bootstrap_default_portfolio(pool, tenant_id)

    _run_alembic_ok("downgrade", _DOWN_REVISION)
    account_id = await _insert_pos_account(pool, tenant_id)
    old_key = f"TESTVENUE:INST{uuid4().hex[:8]}:default:paper"
    await _insert_legacy_pos_snapshot(
        pool, position_key=old_key, tenant_id=tenant_id, account_id=account_id,
        portfolio_id=portfolio_id,
    )
    journal_id = await _insert_legacy_pos_journal(
        pool, position_key=old_key, tenant_id=tenant_id, account_id=account_id
    )

    _run_alembic_ok("upgrade", "head")

    expected_new_key = f"{old_key}:{portfolio_id}"
    try:
        async with pool.acquire() as conn:
            old_row = await conn.fetchrow(
                "SELECT 1 FROM pos_snapshot WHERE position_key = $1", old_key
            )
            new_row = await conn.fetchrow(
                "SELECT tenant_id, account_id, portfolio_id FROM pos_snapshot "
                "WHERE position_key = $1",
                expected_new_key,
            )
            journal_row = await conn.fetchrow(
                "SELECT position_key FROM pos_journal WHERE id = $1", journal_id
            )

        assert old_row is None
        assert new_row is not None
        assert new_row["tenant_id"] == tenant_id
        assert new_row["account_id"] == account_id
        assert new_row["portfolio_id"] == portfolio_id
        # 새 키는 중앙 생성자(PositionKey)가 강제하는 5부분 형식을 통과해야 한다.
        parsed = PositionKey.parse(expected_new_key)
        assert parsed.portfolio_id == portfolio_id
        # WORM: pos_journal은 옛 4부분 키 그대로 남는다(알려진 한계, 마이그레이션 docstring).
        assert journal_row["position_key"] == old_key
    finally:
        # FA-0d(cdb114b6903f)는 pos_snapshot에 남은 행을 하나라도 보면 이후
        # 마이그레이션 왕복 테스트를 fail-closed로 거부한다(task-2543) -- 이
        # 합성 행의 portfolio_id는 이 테스트 안에서만 부트스트랩된 것이라
        # 다른 테스트가 그 사이 entity 테이블을 왕복시키면 재현되지 않는다.
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pos_snapshot WHERE position_key = $1", expected_new_key
            )


async def test_backfill_fails_closed_and_rolls_back_whole_migration_when_portfolio_id_null(
    pool,
):
    resolvable_tenant = await create_test_tenant(pool)
    resolvable_portfolio_id = await bootstrap_default_portfolio(pool, resolvable_tenant)
    bare_tenant = await create_test_tenant(pool)

    _run_alembic_ok("downgrade", _DOWN_REVISION)
    resolvable_account_id = await _insert_pos_account(pool, resolvable_tenant)
    bare_account_id = await _insert_pos_account(pool, bare_tenant)
    resolvable_key = f"TESTVENUE:INST{uuid4().hex[:8]}:default:paper"
    bare_key = f"TESTVENUE:INST{uuid4().hex[:8]}:default:paper"
    await _insert_legacy_pos_snapshot(
        pool, position_key=resolvable_key, tenant_id=resolvable_tenant,
        account_id=resolvable_account_id, portfolio_id=resolvable_portfolio_id,
    )
    await _insert_legacy_pos_snapshot(
        pool, position_key=bare_key, tenant_id=bare_tenant, account_id=bare_account_id,
        portfolio_id=None,
    )

    result = _run_alembic("upgrade", "head")

    try:
        assert result.returncode != 0
        # ASCII marker: the child process console encoding (Windows cp949) would
        # mangle the Korean message text, but never the exception class name.
        assert "UnbackfillablePositionKeyError" in result.stdout + result.stderr

        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            resolvable_row = await conn.fetchrow(
                "SELECT 1 FROM pos_snapshot WHERE position_key = $1", resolvable_key
            )
            bare_row = await conn.fetchrow(
                "SELECT 1 FROM pos_snapshot WHERE position_key = $1", bare_key
            )

        assert version == _DOWN_REVISION
        # 전체 트랜잭션 롤백 -- 백필 가능했던 행도 함께 옛 형식 그대로 남는다.
        assert resolvable_row is not None
        assert bare_row is not None
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pos_snapshot WHERE position_key = ANY($1::varchar[])",
                [resolvable_key, bare_key],
            )
            await conn.execute(
                "DELETE FROM pos_account WHERE account_id = ANY($1::uuid[])",
                [resolvable_account_id, bare_account_id],
            )


async def test_adapter_written_snapshot_survives_fa0d_downgrade_upgrade_round_trip(pool):
    """Gate-red reproduction (task-771991202, CI 77871f67): a snapshot written
    through `PostgresSnapshotRepository.upsert` must carry `portfolio_id` in
    the column (not only inside the key string), otherwise this migration's
    downgrade (5->4 parts) followed by upgrade fails closed and leaves the
    database stuck below head. Red before the adapter fix, green after."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    portfolio_id = default_portfolio_id(tenant_id)
    position_key = str(
        PositionKey(
            venue="TESTVENUE", instrument_id=f"INST{uuid4().hex[:8]}", strategy_id="default",
            execution_id="paper", portfolio_id=portfolio_id,
        )
    )
    await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )
    legacy_key = ":".join(position_key.split(":")[:4])
    try:
        async with pool.acquire() as conn:
            column_value = await conn.fetchval(
                "SELECT portfolio_id FROM pos_snapshot WHERE position_key = $1", position_key
            )
        assert column_value == portfolio_id, "adapter left pos_snapshot.portfolio_id NULL"

        _run_alembic_ok("downgrade", _DOWN_REVISION)
        async with pool.acquire() as conn:
            downgraded = await conn.fetchrow(
                "SELECT portfolio_id FROM pos_snapshot WHERE position_key = $1", legacy_key
            )
        assert downgraded is not None and downgraded["portfolio_id"] == portfolio_id

        _run_alembic_ok("upgrade", "head")
        async with pool.acquire() as conn:
            restored = await conn.fetchrow(
                "SELECT tenant_id, portfolio_id FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )
            leftover = await conn.fetchval(
                "SELECT count(*) FROM pos_snapshot WHERE position_key = $1", legacy_key
            )
        assert restored is not None
        assert restored["tenant_id"] == tenant_id
        assert restored["portfolio_id"] == portfolio_id
        assert leftover == 0
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pos_snapshot WHERE position_key = ANY($1::varchar[])",
                [position_key, legacy_key],
            )
