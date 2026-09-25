"""LB-18 적대적 - 같은 position_key에 동시 체결 20건.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#8.3 LB-18
("같은 position_key에 동시 체결 20건(asyncio.gather) -> seq 1..20 빈틈·중복 없음").

record_fill(LB-11)의 _acquire_position_lock이
pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))으로
같은 position_key를 잠그므로(journal.append가 다시 잡는 같은 락은 같은
트랜잭션 안에서 재진입 - 모듈 docstring 참고), 서로 다른 커넥션·트랜잭션에서
동시에 들어온 20건은 그 락에서 순서대로 줄을 서야 한다. 이 테스트가 검증하려는
것은 "줄을 서는가"이지 어떤 순서로 서는가가 아니다 - 그래서 전부 같은
방향(BUY)으로 수량 1씩만 채워 원가법 계산 순서 자체는 결과에 영향을 주지
않게 하고, 저널 sequence_no 집합이 {1..20}과 정확히 같은지(빈틈도
중복도 없는지)만 본다.

DEEPEN (task-4121): negative test 3건(malformed key, account mismatch,
negative quantity), 실패주입 1건(journal 어댑터 예외 -> 원자성 검증).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import UnknownPositionError, record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENT_FILLS = 20


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=_CONCURRENT_FILLS + 5)
    yield p
    await p.close()


def _key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="race",
        )
    )


async def _delete_pos_snapshot(pool: asyncpg.Pool, *, position_key: str) -> None:
    """FA-0d(cdb114b6903f)는 pos_snapshot에 남은 행을 하나라도 보면 이후
    마이그레이션 왕복 테스트를 fail-closed로 거부한다(task-2543) - 이 행의
    portfolio_id는 합성값이라 실제 FA-4 백필로 재현할 수 없으므로,
    부트스트랩 대신 테스트가 끝나면 직접 지워 그 불변조건(0행)을 지킨다."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pos_snapshot WHERE position_key = $1", position_key)


async def _fill_once(
    pool: asyncpg.Pool, *, tenant_id: UUID, account_id: UUID, position_key: str
) -> None:
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    command = RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.KRW),
        fee=None,
        occurred_at=_OCCURRED_AT,
        trace_id=uuid4(),
    )
    async with pool.acquire() as conn, conn.transaction():
        await record_fill(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=journal,
            snapshots=snapshots,
            audit=audit,
            clock=_clock,
        )


# ---------------------------------------------------------------------------
# Negative tests (3건 이상)
# ---------------------------------------------------------------------------


async def test_malformed_position_key_rejected(pool: asyncpg.Pool) -> None:
    """LB-18 negative: f-string/concat로 만든 position_key -> InvalidPositionKeyError."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    # 의도적으로 broken key - PositionKey.parse()가 reject해야 함
    bad_key = "broken:portfolio:venue:INST:badkey"
    command = RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=bad_key,
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.KRW),
        fee=None,
        occurred_at=_OCCURRED_AT,
        trace_id=uuid4(),
    )
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    with pytest.raises(ValueError):
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                command,
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )


async def test_account_mismatch_rejected(pool: asyncpg.Pool) -> None:
    """LB-18 negative: 같은 tenant, 다른 account -> UnknownPositionError."""
    owner_id = await create_test_tenant(pool)
    owner_account_id = await create_pos_account(pool, owner_id)
    attacker_account_id = await create_pos_account(pool, owner_id)
    position_key = _key(owner_id)
    # position은 owner_account_id에 열었으나 attacker_account_id로 fill 시도
    await open_position(
        pool, tenant_id=owner_id, account_id=owner_account_id, position_key=position_key
    )
    try:
        command = RecordFillCommand(
            tenant_id=owner_id,
            account_id=attacker_account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )
        journal = PostgresJournalRepository(pool)
        snapshots = PostgresSnapshotRepository(pool)
        audit = PostgresAuditEventRepository(pool)
        with pytest.raises(UnknownPositionError):
            async with pool.acquire() as conn, conn.transaction():
                await record_fill(
                    conn,
                    command,
                    asset_class=AssetClass.CRYPTO,
                    journal=journal,
                    snapshots=snapshots,
                    audit=audit,
                    clock=_clock,
                )
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


async def test_sell_beyond_quantity_rejected(pool: asyncpg.Pool) -> None:
    """LB-18 negative: 현재 포지션 수량보다 큰 Sell -> NegativeQuantityError."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    # BUY 5로 포지션 열기
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    try:
        # BUY 5 먼저 기록해서 quantity=5가 되게 함
        for i in range(5):
            command = RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=i + 1,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Money(amount=Decimal("100"), currency=Currency.KRW),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            )
            journal = PostgresJournalRepository(pool)
            snapshots = PostgresSnapshotRepository(pool)
            audit = PostgresAuditEventRepository(pool)
            async with pool.acquire() as conn, conn.transaction():
                await record_fill(
                    conn,
                    command,
                    asset_class=AssetClass.CRYPTO,
                    journal=journal,
                    snapshots=snapshots,
                    audit=audit,
                    clock=_clock,
                )

        # 이제 quantity=5인데, Sell 10 -> NegativeQuantityError
        command = RecordFillCommand(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=6,
            side=OrderSide.SELL,
            quantity=Decimal("10"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )
        journal = PostgresJournalRepository(pool)
        snapshots = PostgresSnapshotRepository(pool)
        audit = PostgresAuditEventRepository(pool)
        with pytest.raises(NegativeQuantityError):
            async with pool.acquire() as conn, conn.transaction():
                await record_fill(
                    conn,
                    command,
                    asset_class=AssetClass.CRYPTO,
                    journal=journal,
                    snapshots=snapshots,
                    audit=audit,
                    clock=_clock,
                )
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


# ---------------------------------------------------------------------------
# Failure injection test
# ---------------------------------------------------------------------------


async def test_journal_write_failure_preserves_atomicity(pool: asyncpg.Pool) -> None:
    """LB-18 실패주입: journal.append가 예외를 던지면 저널/스냅샷에
    부분 기록이 남아서는 안 된다 - 트랜잭션 원자성 보장."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    try:
        # 기존 quantity/last_journal_seq 확인
        async with pool.acquire() as conn:
            before_qty = await conn.fetchval(
                "SELECT quantity FROM pos_snapshot WHERE position_key = $1", position_key
            )
            before_seq = await conn.fetchval(
                "SELECT last_journal_seq FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )
            before_journal_count = await conn.fetchval(
                "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
            )

        journal = PostgresJournalRepository(pool)
        snapshots = PostgresSnapshotRepository(pool)
        audit = PostgresAuditEventRepository(pool)
        command = RecordFillCommand(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )

        # journal.append를 monkeypatch해서 예외 유발
        async def _boom_append(*args: object, **kwargs: object) -> None:
            raise asyncpg.PostgresConnectionError("simulated connection loss")

        with patch.object(journal, "append", _boom_append):
            with pytest.raises(asyncpg.PostgresConnectionError):
                async with pool.acquire() as conn, conn.transaction():
                    await record_fill(
                        conn,
                        command,
                        asset_class=AssetClass.CRYPTO,
                        journal=journal,
                        snapshots=snapshots,
                        audit=audit,
                        clock=_clock,
                    )

        # 트랜잭션 롤백 후 원자성 검증
        async with pool.acquire() as conn:
            after_journal_count = await conn.fetchval(
                "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
            )
            snapshot_row = await conn.fetchrow(
                "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )

        assert after_journal_count == before_journal_count, (
            "journal.append 실패에도 저널 행이 추가됨(트랜잭션 미롤백)"
        )
        assert snapshot_row["quantity"] == before_qty, "스냅샷 수량이 변경됨(롤백 실패)"
        assert snapshot_row["last_journal_seq"] == before_seq, (
            "last_journal_seq가 증가함(롤백 실패)"
        )
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


# ---------------------------------------------------------------------------
# Original concurrent serializability test
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_concurrent_fills_serializable(pool: asyncpg.Pool) -> None:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    try:
        started = time.perf_counter()
        results = await asyncio.gather(
            *[
                _fill_once(
                    pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
                )
                for _ in range(_CONCURRENT_FILLS)
            ],
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - started

        failures = [r for r in results if isinstance(r, BaseException)]
        assert failures == []  # 락이 제대로 걸리면 전부 성공해야 한다 - 실패는 곧 경쟁상태.
        # 수치 성능 단언 (DEPTH_FA 감사 task-3034/FA-0d 유일 미달 항목): 20개 워커가
        # 모두 같은 position_key의 pg_advisory_xact_lock에서 줄을 서므로(모듈
        # 독스트링) 대기는 본질적으로 순차지만, 락이 풀리지 않거나(교착) 재시도
        # 폭주로 퇴화하면 이 예산을 넘긴다 - 수치 상한이 없으면 그런 회귀를
        # 놓친다.
        assert elapsed < 10.0, (
            f"20건 동시 체결(pg_advisory_xact_lock 직렬화)이 {elapsed:.3f}s - 예산(10.0s) 초과"
        )

        async with pool.acquire() as conn:
            seqs = [
                row["sequence_no"]
                for row in await conn.fetch(
                    "SELECT sequence_no FROM pos_journal WHERE position_key = $1 "
                    "ORDER BY sequence_no",
                    position_key,
                )
            ]
            snapshot_row = await conn.fetchrow(
                "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )

        assert seqs == list(range(1, _CONCURRENT_FILLS + 1)), "저널 sequence_no에 빈틈 또는 중복"
        assert len(set(seqs)) == _CONCURRENT_FILLS
        assert snapshot_row["quantity"] == Decimal(_CONCURRENT_FILLS)
        assert snapshot_row["last_journal_seq"] == _CONCURRENT_FILLS
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)
