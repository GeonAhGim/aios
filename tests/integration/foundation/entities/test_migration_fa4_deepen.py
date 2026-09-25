"""FA-4(963d5f3cfb1b) DEEPEN(task-3010) — 부족 증빙 보강.

task-2724 DEPTH 감사(docs/audit/DEPTH_FA.md)가 원 task-1794를 D1로 매긴
근거: `test_migration_fa4_columns.py`(3함수)+`test_migration_fa4_worm_no_backfill.py`
(3함수)에 negative가 fund_id FK 거부 1건 + WORM 대차 불변 회귀 1건, 총 2건뿐이고
(ADR-2026-09-09-C D2 하한은 negative>=3), 성능단언이 없고, FA는 안전축이라
D3(적대/리플레이/다중인스턴스) 증거가 필요한데 전혀 없었다.

이 파일이 새로 증명하는 것(원 두 파일은 건드리지 않는다 — 이미 정착된 라운드
트립/백필/WORM 테스트를 재작성하지 않고 부족분만 보탠다):
  1~3. negative 3건 추가: pos_account.portfolio_id, pos_snapshot.fund_id,
     pos_snapshot.portfolio_id 각각의 FK 거부 — 원 두 파일의 2건과 합쳐
     이 리프 총 negative 5건.
  4~5. 적대적(D3): 마이그레이션이 "영구 NULL"이라고 주장하는 WORM 3테이블 중
     `pos_journal`/`ledger_journal_entry`에 대해, 백필이 아니라 head 스키마에서
     일반 UPDATE로 fund_id를 직접 덮어쓰려는 시도조차 WORM 가드 트리거
     (`*_worm_guard_trg`)가 막는지 확인한다 — "백필 안 함"이 아니라
     "백필이 물리적으로 불가능함"을 증명한다.
  6. 성능단언: 부트스트랩된 tenant 50명의 소급 백필(pos_account+pos_snapshot)이
     예산 안에서 끝난다.
  7. D3 리플레이: downgrade -> upgrade 사이클을 두 번 반복해도 pos_account
     백필 결과값(fund_id/portfolio_id)이 동일하다 — `default_fund_id`/
     `default_portfolio_id`가 tenant_id의 순수 함수(UUIDv5)라는 전제를 실제
     마이그레이션 경로로 검증한다.
  8. D3 다중 인스턴스: 마이그레이션 본문의 백필 UPDATE(963d5f3cfb1b:87-98)를
     그대로 재현한 SQL을 같은 대상 행에 대해 여러 커넥션에서 동시에 실행해도
     경합/교착 없이 같은 정답에 수렴한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from tests.integration.conftest import create_test_tenant
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "789c138f13fe"
_PERF_BUDGET_SECONDS = 10.0
_PERF_TENANT_COUNT = 50
_CONCURRENT_RUNNERS = 8

# 963d5f3cfb1b:87-98 pos_account 백필 UPDATE의 사본(asyncpg 위치 파라미터로만 변환).
_BACKFILL_UPDATE_SQL = """
    UPDATE pos_account SET fund_id = $1, portfolio_id = $2
    WHERE tenant_id = $3
    AND EXISTS (SELECT 1 FROM fund WHERE fund_id = $1)
    AND EXISTS (
      SELECT 1 FROM portfolio
      WHERE portfolio_id = $2 AND fund_id = $1
    )
    """


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
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=max(4, _CONCURRENT_RUNNERS))
    yield p
    await p.close()


def _sweep_synthetic_snapshots(prefix: str) -> None:
    """FA-0d-fix (task-771991202): rows this module inserts below FA-4 carry
    synthetic non-5-part keys that `cdb114b6903f` (FA-0d) refuses fail-closed,
    so they are removed before the schema is brought back to head."""

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
    _sweep_synthetic_snapshots("fa4-deepen-test-")
    _run_alembic("upgrade", "head")


async def _insert_bare_pos_account(conn: asyncpg.Connection, tenant_id: UUID) -> UUID:
    return await conn.fetchval(
        "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
        "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
        tenant_id,
    )


async def test_negative_insert_pos_account_with_nonexistent_portfolio_id_rejected_by_fk(pool):
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method, "
                "portfolio_id) VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO', $2)",
                tenant_id,
                uuid4(),
            )


async def test_negative_insert_pos_snapshot_with_nonexistent_fund_id_rejected_by_fk(pool):
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await _insert_bare_pos_account(conn, tenant_id)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
                "quantity, cost_method, fund_id) VALUES ($1, $2, $3, $4, 0, 'FIFO', $5)",
                f"fa4-deepen-test-{uuid4().hex}",
                tenant_id,
                account_id,
                uuid4(),
                uuid4(),
            )


async def test_negative_insert_pos_snapshot_with_nonexistent_portfolio_id_rejected_by_fk(pool):
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await _insert_bare_pos_account(conn, tenant_id)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
                "quantity, cost_method, portfolio_id) VALUES ($1, $2, $3, $4, 0, 'FIFO', $5)",
                f"fa4-deepen-test-{uuid4().hex}",
                tenant_id,
                account_id,
                uuid4(),
                uuid4(),
            )


async def test_adversarial_direct_update_of_pos_journal_fund_id_blocked_by_worm_trigger(pool):
    """WORM 가드는 마이그레이션의 백필 UPDATE만 막는 게 아니라 head 스키마에서
    일반적인 UPDATE로 fund_id를 나중에 채우려는 시도 자체를 물리적으로 막는다
    — "백필 안 함"이 아니라 "백필이 불가능함"을 증명한다."""
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await _insert_bare_pos_account(conn, tenant_id)
        position_key = f"fa4-deepen-test-{uuid4().hex}"
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
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa4-deepen-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa4-deepen-test-{uuid4().hex}",
        )

    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pos_journal SET fund_id = $1 WHERE id = $2", uuid4(), journal_id
            )


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(pool: asyncpg.Pool, user_id: UUID) -> str:
    code = user_account(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, FALSE) RETURNING account_id",
            code,
            AccountType.LIABILITY.value,
            Currency.KRW.value,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, FALSE, 0)",
            account_id,
        )
    return code


def _topup_event(*, event_ref: str, user_id: UUID) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("10.00"),
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )


class _RealPorts:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


async def _post_topup(pool: asyncpg.Pool, ports: _RealPorts, event_ref: str):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=event_ref, user_id=user_id)
    async with pool.acquire() as conn, conn.transaction():
        return await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )


async def test_adversarial_direct_update_of_ledger_journal_entry_fund_id_blocked_by_worm_trigger(
    pool,
):
    """`ledger_journal_entry`도 pos_journal과 같은 WORM 가드 패턴
    (`*_worm_guard_trg`)이 걸려 있다 — 실제 post_entry(LC-9)로 만든 정상
    entry라도 fund_id를 나중에 UPDATE로 채우려는 시도는 거부되어야 한다."""
    ports = _RealPorts(pool)
    view = await _post_topup(pool, ports, f"fa4-deepen-test:{uuid4().hex}")

    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_journal_entry SET fund_id = $1 WHERE entry_id = $2",
                uuid4(),
                view.entry_id,
            )


@pytest.mark.perf
async def test_backfill_of_fifty_bootstrapped_tenants_completes_within_budget(pool):
    # 성능단언 — 감사가 지적한 "성능단언 없음" 공백을 메운다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _DOWN_REVISION)

    tenant_ids = []
    for _ in range(_PERF_TENANT_COUNT):
        tenant_id = await create_test_tenant(pool)
        async with pool.acquire() as conn:
            account_id = await _insert_bare_pos_account(conn, tenant_id)
            await conn.execute(
                "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
                "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
                f"fa4-deepen-test-{uuid4().hex}",
                tenant_id,
                account_id,
                uuid4(),
            )
        tenant_ids.append(tenant_id)

    started = time.monotonic()
    _run_alembic("upgrade", "963d5f3cfb1b")
    elapsed = time.monotonic() - started

    assert elapsed < _PERF_BUDGET_SECONDS, (
        f"{_PERF_TENANT_COUNT}명 백필이 예산({_PERF_BUDGET_SECONDS}s)을 넘겼다: {elapsed:.2f}s"
    )

    async with pool.acquire() as conn:
        pos_account_backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_account WHERE tenant_id = ANY($1::uuid[]) "
            "AND fund_id IS NOT NULL",
            tenant_ids,
        )
        pos_snapshot_backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE tenant_id = ANY($1::uuid[]) "
            "AND fund_id IS NOT NULL",
            tenant_ids,
        )
    assert pos_account_backfilled == _PERF_TENANT_COUNT
    assert pos_snapshot_backfilled == _PERF_TENANT_COUNT


async def test_backfill_is_deterministic_across_repeated_downgrade_upgrade_cycles(pool):
    # D3 리플레이 — 같은 사이클을 두 번 반복해도 백필값이 항상 같다.
    tenant_id = await create_test_tenant(pool)

    results = []
    for _ in range(2):
        await purge_position_snapshots(pool)
        _run_alembic("downgrade", _DOWN_REVISION)
        async with pool.acquire() as conn:
            account_id = await _insert_bare_pos_account(conn, tenant_id)

        _run_alembic("upgrade", "head")

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT fund_id, portfolio_id FROM pos_account WHERE account_id = $1", account_id
            )
        results.append((row["fund_id"], row["portfolio_id"]))

    assert results[0] == results[1]
    assert results[0] == (default_fund_id(tenant_id), default_portfolio_id(tenant_id))


async def test_concurrent_backfill_runners_converge_to_the_same_correct_state(pool):
    # D3 다중 인스턴스 — 여러 마이그레이터 인스턴스가 락 획득 전 겹쳐 같은
    # 백필 UPDATE를 동시에 쏘는 상황을 흉내낸다. 목표 행은 하나뿐이므로
    # "경합 후 정답 하나로 수렴 + 에러/교착 없음"이 곧 안전성 증명이다.
    tenant_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        account_id = await _insert_bare_pos_account(conn, tenant_id)

    fund_id = default_fund_id(tenant_id)
    portfolio_id = default_portfolio_id(tenant_id)

    async def _run_once() -> None:
        async with pool.acquire() as conn:
            await conn.execute(_BACKFILL_UPDATE_SQL, fund_id, portfolio_id, tenant_id)

    await asyncio.gather(*(_run_once() for _ in range(_CONCURRENT_RUNNERS)))

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_account WHERE account_id = $1", account_id
        )

    assert row["fund_id"] == fund_id
    assert row["portfolio_id"] == portfolio_id
