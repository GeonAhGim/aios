"""PLT-30 — 레거시 테이블(orders/positions/strategy_executions)은 정책만
갖고 RLS는 비활성 상태로 남는다 + M5 마이그레이션 upgrade/downgrade 왕복.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §10 리스크1
("레거시 테이블은 정책만 만들고 ENABLE하지 않는다 — 기존 pool.acquire()
경로가 0행을 받아 깨지는 것을 막는다"), §9 PLT-30 DoD("upgrade/downgrade
왕복").

task-5823 root-cause fix: `test_upgrade_downgrade_round_trip` used to run its
real `alembic downgrade`/`upgrade head` round trip directly against the
process's shared `DATABASE_URL` -- the same session-lifetime DB
`scripts/replay_verify.py` and every other test reads. `_PRE_RLS_REVISION`
(5a0aedee0af0) sits well before 073beca589d5 in the migration chain, so this
downgrade also drops `order_events`; a process kill or a concurrent reader
landing inside the downgrade window before the `finally: upgrade head` runs
permanently leaves the *shared* DB without `order_events` for the rest of the
CI run -- exactly esc-ci-replay_verify's `UndefinedTableError:
"order_events"`. Same root cause task-5783/5794/5795 already fixed for
tests/integration/oms/test_db_transition_trigger.py,
tests/integration/foundation/entities/test_migration_roundtrip.py and
tests/integration/db/test_migration_tenant_membership.py. The round trip now
runs against its own disposable DB clone
(`tests/support/db.ensure_worker_database`) so an interrupted downgrade can
never corrupt state anything else reads.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from tests.integration.conftest import create_test_user
from tests.integration.core.db.conftest import AppRoleTx
from tests.support.db import ensure_worker_database, template_database_url
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# b3c7f19ad2e6(rls_policies_foundation, PLT-30 M5)의 down_revision. 이 리프
# 작성 시점엔 head와 인접해 "downgrade -1"로 되돌릴 수 있었지만, 그 뒤 두
# 마이그레이션(execution_leases EO-02, DC-4)이 head 위에 더 쌓이면서
# "-1"이 더 이상 이 리비전을 가리키지 않게 됐다(esc-ci-cbb8b9c62497) — 위치
# 대신 리비전 id를 직접 지정해 head가 계속 자라도 이 왕복 테스트가 항상
# b3c7f19ad2e6 자체를 되돌리도록 고정한다.
_PRE_RLS_REVISION = "5a0aedee0af0"
_LEGACY_TABLES = ("orders", "positions", "strategy_executions")
_FOUNDATION_TABLES = (
    "consent_record",
    "account_connection",
    "risk_evaluation",
    "paper_deployment",
    "reconciliation_run",
    "valuation_snapshot",
    "portfolio_mandate",
    "foundation_audit_event",
)


async def _insert_position(pool: asyncpg.Pool, user_id: UUID, strategy_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions "
            "(user_id, symbol, exchange, strategy_id, quantity, average_entry_price, "
            "entry_time) "
            "VALUES ($1, 'BTC/USDT', 'bitget', $2, 1, 100, now())",
            user_id,
            strategy_id,
        )


async def _relrowsecurity(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", table
        )
    return bool(value)


async def _policy_exists(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE tablename = $1 AND policyname = "
            "'tenant_isolation'",
            table,
        )
    return bool(value)


@pytest.mark.parametrize("table", _LEGACY_TABLES)
async def test_legacy_table_has_policy_but_rls_disabled(pool, table):
    assert await _policy_exists(pool, table)
    assert await _relrowsecurity(pool, table) is False


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
async def test_foundation_table_has_policy_and_rls_enabled(pool, table):
    assert await _policy_exists(pool, table)
    assert await _relrowsecurity(pool, table) is True


async def test_legacy_pool_acquire_without_tenant_binding_still_sees_all_rows(pool):
    """RLS가 걸렸다면 GUC 미설정 시 0행이 됐을 것 — 레거시 테이블은 정책이
    있어도 ENABLE하지 않았으므로 기존 서비스 경로(트랜잭션 밖 pool.acquire(),
    app.tenant_id 미설정)가 그대로 동작해야 한다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    strategy_id = f"rls-legacy-{user_a.hex[:8]}"
    await _insert_position(pool, user_a, strategy_id)
    await _insert_position(pool, user_b, strategy_id)

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch("SELECT user_id FROM positions WHERE strategy_id = $1", strategy_id)

    assert {r["user_id"] for r in rows} == {user_a, user_b}


@pytest.mark.parametrize("table", _LEGACY_TABLES)
async def test_aios_app_cannot_enable_rls_on_legacy_table(pool, table):
    """레거시 테이블의 RLS 비활성 상태는 소유권 없는 `aios_app`이 되돌릴 수
    없어야 한다 — ENABLE도 ALTER TABLE(DDL, 소유자 전용)이라
    tests/adversarial/ledger/test_role_bypass.py·test_rls_bypass_denied.py와
    동일하게 권한 검사 자체에서 막혀야 한다(이 불변식이 우연이 아니라
    권한 구조로 보장됨을 확인)."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


@pytest.mark.parametrize("table", _LEGACY_TABLES)
async def test_aios_app_cannot_drop_tenant_isolation_policy_on_legacy_table(pool, table):
    """레거시 테이블도 정책 자체는 갖고 있다(ENABLE만 안 함) — 그 정책을
    `aios_app`이 지울 수 없어야 한다. DROP POLICY도 소유자 전용 DDL."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"DROP POLICY tenant_isolation ON {table}")


@pytest.mark.parametrize("table", _LEGACY_TABLES)
async def test_aios_app_cannot_create_permissive_bypass_policy_on_legacy_table(pool, table):
    """RLS가 이미 꺼져 있어도 정책 목록 자체를 `aios_app`이 조작할 수 없어야
    한다 — CREATE POLICY도 소유자 전용 DDL(test_rls_bypass_denied.py가
    foundation 테이블에서 확인한 것과 같은 케이스를, 거기서 다루지 않은
    레거시 테이블에도 적용해 회귀를 잡는다)."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"CREATE POLICY bypass_everything ON {table} USING (true)")


def _run_alembic(*args: str, database_url: str | None = None) -> None:
    env = {**os.environ, "DATABASE_URL": database_url} if database_url else None
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


def test_run_alembic_raises_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch):
    """`_run_alembic`은 `test_upgrade_downgrade_round_trip`의 downgrade/upgrade
    양쪽 호출을 모두 거친다 — subprocess가 실패를 리턴해도 이를 삼키고
    조용히 넘어가면, 왕복 테스트가 실제로는 마이그레이션이 실패했는데도
    그린으로 통과하는 회귀가 생긴다. `subprocess.run`에 returncode=1을
    주입해 fail-closed(AssertionError)로 즉시 드러나는지 확인한다."""

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else kwargs.get("args", []),
            returncode=1,
            stdout="",
            stderr="alembic boom",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(AssertionError, match="alembic boom"):
        _run_alembic("upgrade", "head")


async def test_upgrade_downgrade_round_trip():
    """Disposable DB clone (task-5823) -- never the shared session DB other
    tests and `scripts/replay_verify.py` depend on. See module docstring."""
    migration_db_url = await ensure_worker_database(template_database_url(), "rlslegacyrt")
    migration_pool = await asyncpg.create_pool(
        migration_db_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1,
        max_size=2,
    )
    try:
        try:
            await purge_position_snapshots(
                migration_pool
            )  # deep downgrade: see tests/support/deep_downgrade.py
            _run_alembic("downgrade", _PRE_RLS_REVISION, database_url=migration_db_url)
            for table in _FOUNDATION_TABLES:
                assert await _relrowsecurity(migration_pool, table) is False
                assert not await _policy_exists(migration_pool, table)
            for table in _LEGACY_TABLES:
                assert not await _policy_exists(migration_pool, table)
        finally:
            _run_alembic("upgrade", "head", database_url=migration_db_url)

        for table in _FOUNDATION_TABLES:
            assert await _relrowsecurity(migration_pool, table) is True
            assert await _policy_exists(migration_pool, table)
        for table in _LEGACY_TABLES:
            assert await _policy_exists(migration_pool, table)
            assert await _relrowsecurity(migration_pool, table) is False
    finally:
        await migration_pool.close()
