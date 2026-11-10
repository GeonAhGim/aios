"""LB-18 적대적 - 실패주입: journal 쓰기 예외 시 트랜잭션 원자성.

test_race_fills.py에서 책임(실패주입)별로 분리했다. fixture/헬퍼는 그 모듈을 재사용한다.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pytest

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
from tests.foundation.adversarial.positions.test_race_fills import (
    _OCCURRED_AT,
    _clock,
    _delete_pos_snapshot,
    _key,
    pool,  # noqa: F401 - fixture reuse
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

# ---------------------------------------------------------------------------
# Failure injection test
# ---------------------------------------------------------------------------


async def test_journal_write_failure_preserves_atomicity(pool: asyncpg.Pool) -> None:  # noqa: F811
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
