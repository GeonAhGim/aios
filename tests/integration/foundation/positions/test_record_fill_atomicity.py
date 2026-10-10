"""LB-11 거부·감사 실패 시 원자성 및 감사 1:1 검증."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from tests.integration.foundation.positions.record_fill_fixtures import (
    _BoomAuditAppender,
    _command,
    _open,
    _record,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    ports as ports,
)


async def test_reverse_direction_oversell_rejected_and_not_persisted(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
            order_id=order_id,
            fill_seq=1,
        ),
    )

    with pytest.raises(NegativeQuantityError):
        await _record(
            pool,
            ports,
            _command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                side=OrderSide.SELL,
                quantity=Decimal("15"),
                price=Decimal("120"),
                order_id=order_id,
                fill_seq=2,
            ),
        )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 1
    assert snapshot_row["quantity"] == Decimal("10")
    assert snapshot_row["last_journal_seq"] == 1


async def test_each_new_fill_emits_exactly_one_audit_event(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    fills = [
        (OrderSide.BUY, Decimal("10")),
        (OrderSide.BUY, Decimal("5")),
        (OrderSide.SELL, Decimal("3")),
    ]
    for fill_seq, (side, qty) in enumerate(fills, start=1):
        await _record(
            pool,
            ports,
            _command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                side=side,
                quantity=qty,
                price=Decimal("100"),
                order_id=order_id,
                fill_seq=fill_seq,
            ),
        )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", order_id
        )
    assert journal_count == 3
    assert audit_count == 3


async def test_audit_failure_rolls_back_journal_and_snapshot(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
    )

    with pytest.raises(RuntimeError):
        await _record(pool, ports, command, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0
    assert snapshot_row["quantity"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0
