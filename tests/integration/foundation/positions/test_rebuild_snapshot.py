"""LB-13 `rebuild_snapshot` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9.3 LB-13.
DoD: "재빌드 drift ∅" — 정상 스냅샷은 dry-run이든 아니든 drift가 비어야
하고, 스냅샷이 저널과 어긋나면(변조·버그) 재빌드가 그 차이를 drift로
보고하고 `dry_run=False`일 때만 실제로 고친다. `pos_journal`은 이 리프가
절대 건드리지 않는다(WORM) — 아래 테스트는 재빌드 전후로 저널 행 수가
그대로임을 확인해 이를 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

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
from src.foundation.positions.application.rebuild_snapshot import (
    UnknownPositionError,
    rebuild_snapshot,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.application.record_funding_fee import record_funding_fee
from src.foundation.positions.contracts.v1 import RecordFillCommand, RecordFundingCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _open(pool):
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key()
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _fill(
    pool, ports, *, tenant_id, account_id, position_key, side, quantity, price, fill_seq, order_id
):
    async with pool.acquire() as conn, conn.transaction():
        return await record_fill(
            conn,
            RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=order_id,
                fill_seq=fill_seq,
                side=side,
                quantity=quantity,
                price=Money(amount=price, currency=Currency.KRW),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )


async def _funding(pool, ports, *, tenant_id, account_id, position_key, amount, funding_id):
    async with pool.acquire() as conn, conn.transaction():
        return await record_funding_fee(
            conn,
            RecordFundingCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                funding_id=funding_id,
                amount=Money(amount=amount, currency=Currency.KRW),
                rate=Decimal("0.0001"),
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            ),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=ports.audit,
            clock=_clock,
        )


async def test_unknown_position_rejected(pool, ports):
    with pytest.raises(UnknownPositionError):
        await rebuild_snapshot(
            _key(),
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
        )


async def test_healthy_snapshot_has_no_drift(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.BUY, quantity=Decimal("10"), price=Decimal("100"),
        fill_seq=1, order_id=order_id,
    )
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.SELL, quantity=Decimal("4"), price=Decimal("120"),
        fill_seq=2, order_id=order_id,
    )
    await _funding(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        amount=Decimal("-2"), funding_id=str(uuid4()),
    )

    report = await rebuild_snapshot(
        position_key,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.drift == {}
    assert report.applied is False
    assert report.entries == 3


async def test_dry_run_reports_drift_without_writing(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.BUY, quantity=Decimal("10"), price=Decimal("100"),
        fill_seq=1, order_id=order_id,
    )

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pos_snapshot SET quantity = 999, realized_pnl_base = 42 "
            "WHERE position_key = $1",
            position_key,
        )

    report = await rebuild_snapshot(
        position_key,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.applied is False
    assert report.drift["quantity"] == (Decimal("999"), Decimal("10"))
    assert report.drift["realized_pnl_base"] == (Decimal("42"), Decimal("0"))

    async with pool.acquire() as conn:
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert snapshot_row["quantity"] == Decimal("999")
    assert snapshot_row["realized_pnl_base"] == Decimal("42")


async def test_apply_fixes_drift_without_touching_journal(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.BUY, quantity=Decimal("10"), price=Decimal("100"),
        fill_seq=1, order_id=order_id,
    )
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.SELL, quantity=Decimal("4"), price=Decimal("120"),
        fill_seq=2, order_id=order_id,
    )

    async with pool.acquire() as conn:
        journal_count_before = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        await conn.execute(
            "UPDATE pos_snapshot SET quantity = 0, realized_pnl_base = 0 WHERE position_key = $1",
            position_key,
        )

    report = await rebuild_snapshot(
        position_key,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is True
    assert report.drift["quantity"] == (Decimal("0"), Decimal("6"))
    assert report.drift["realized_pnl_base"] == (
        Decimal("0"),
        (Decimal("120") - Decimal("100")) * Decimal("4"),
    )

    async with pool.acquire() as conn:
        journal_count_after = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base, last_journal_seq FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert journal_count_after == journal_count_before == 2
    assert snapshot_row["quantity"] == Decimal("6")
    assert snapshot_row["realized_pnl_base"] == (Decimal("120") - Decimal("100")) * Decimal("4")
    assert snapshot_row["last_journal_seq"] == 2


async def test_apply_with_no_drift_is_noop(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    await _fill(
        pool, ports, tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        side=OrderSide.BUY, quantity=Decimal("10"), price=Decimal("100"),
        fill_seq=1, order_id=uuid4(),
    )

    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )

    report = await rebuild_snapshot(
        position_key,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is False
    assert report.drift == {}

    async with pool.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert before["updated_at"] == after["updated_at"]
