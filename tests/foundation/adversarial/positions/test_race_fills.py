"""LB-18 적대적 — 같은 position_key에 동시 체결 20건.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LB-18
("같은 position_key에 동시 체결 20건(asyncio.gather) → seq 1..20 빈틈·중복 없음").

`record_fill`(LB-11, [[record_fill]])의 `_acquire_position_lock`이
`pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))`으로
같은 `position_key`를 잠그므로(`journal.append`가 다시 잡는 같은 락은 같은
트랜잭션 안에서 재진입 — 모듈 docstring 참고), 서로 다른 커넥션·트랜잭션에서
동시에 들어온 20건은 그 락에서 순서대로 줄을 서야 한다. 이 테스트가 검증하려는
것은 "줄을 서는가"이지 어떤 순서로 서는가가 아니다 — 그래서 전부 같은
방향(BUY)으로 수량 1씩만 채워 원가법 계산 순서 자체는 결과에 영향을 주지
않게 하고, 저널 `sequence_no` 집합이 `{1..20}`과 정확히 같은지(빈틈도
중복도 없는지)만 본다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
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
async def pool():
    p = await asyncpg.create_pool(
        _asyncpg_dsn(), min_size=1, max_size=_CONCURRENT_FILLS + 5
    )
    yield p
    await p.close()


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="race",
        )
    )


async def _fill_once(pool: asyncpg.Pool, *, tenant_id, account_id, position_key) -> None:
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


async def test_twenty_concurrent_fills_produce_gapless_unique_sequence(pool):
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key()
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)

    results = await asyncio.gather(
        *[
            _fill_once(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
            for _ in range(_CONCURRENT_FILLS)
        ],
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures == []  # 락이 제대로 걸리면 전부 성공해야 한다 — 실패는 곧 경쟁상태.

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
