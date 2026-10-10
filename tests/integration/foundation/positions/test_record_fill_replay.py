"""LB-11 동일 체결 재전송 및 payload 충돌 검증."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.adapters.postgres_journal_repository import (
    IdempotencyDigestMismatchError,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    _command,
    _open,
    _record,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    ports as ports,
)


async def test_replay_same_fill_returns_existing_without_duplicate(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
    )

    first = await _record(pool, ports, command)
    second = await _record(pool, ports, command)

    assert second.quantity == first.quantity
    assert second.last_journal_seq == first.last_journal_seq

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1",
            command.order_id,
        )
    assert journal_count == 1
    assert audit_count == 1


async def test_digest_mismatch_same_key_different_content_rejected(pool, ports):
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

    with pytest.raises(IdempotencyDigestMismatchError):
        await _record(
            pool,
            ports,
            _command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                side=OrderSide.BUY,
                quantity=Decimal("99"),
                price=Decimal("100"),
                order_id=order_id,
                fill_seq=1,
            ),
        )


async def test_replay_of_position_closing_fill_skips_cost_basis_recompute(pool, ports):
    """게이트 적색 재현 -- 모듈 docstring(record_fill.py:15-19)이 문서화한
    위험을 실제로 재현한다: 멱등 재입력은 원가법을 다시 계산하지 않고
    `idempotency_key` EXISTS만으로 REPLAY 후보를 판별한다(`is_replay_candidate`).
    이 가드가 없다면(예: 실수로 원가법을 항상 재계산하도록 되돌리면) 아래
    시나리오는 `NegativeQuantityError`를 잘못 던진다 -- 재전송은 반드시
    성공해야 하는데도 적색이 된다: 포지션을 전량청산(BUY 10 -> SELL 10,
    lots=[], quantity=0)한 뒤 **같은 청산 체결을 그대로 재전송**하면, 이미
    빈 로트 큐 위에 SELL 10을 다시 적용하는 셈이라 원가법을 재계산했다면
    보유 로트 합(0)을 초과하는 매도로 거부된다. 이 함수는 REPLAY를
    idempotency_key로 먼저 걸러 원가법 계산 자체를 건너뛰므로 예외 없이
    기존 스냅샷을 그대로 반환해야 한다."""
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    close_command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("10"),
        price=Decimal("120"),
        order_id=order_id,
        fill_seq=2,
    )
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
    closed = await _record(pool, ports, close_command)
    assert closed.quantity == Decimal("0")
    assert closed.lots == []

    replayed = await _record(pool, ports, close_command)

    assert replayed.quantity == Decimal("0")
    assert replayed.lots == []
    assert replayed.last_journal_seq == closed.last_journal_seq

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
    assert journal_count == 2


async def test_replay_skips_realized_pnl_recompute_and_single_upsert(pool, ports):
    """F2(1) REPLAY -- 동일 order_id+fill_seq를 2회 `record_fill`하면
    두 번째 호출은 원가법을 다시 계산하지 않고(realized_pnl_base 불변) 저널/
    스냅샷/감사이벤트 모두 최초 1회분만 남아야 한다. `snapshots.upsert` 호출
    횟수를 직접 세어 REPLAY가 두 번째 upsert를 만들지 않는다는 것까지 증명한다
    -- 최종 DB 상태만 보면 "우연히 같은 값으로 덮어썼다"와 "애초에 쓰지
    않았다"를 구분할 수 없기 때문이다."""
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
    sell_command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        order_id=order_id,
        fill_seq=2,
    )
    first = await _record(pool, ports, sell_command)
    assert first.realized_pnl_base == (Decimal("120") - Decimal("100")) * Decimal("4")

    upsert_calls = 0
    real_upsert = PostgresSnapshotRepository.upsert

    async def _counting_upsert(self, conn, snapshot, expected_seq):
        nonlocal upsert_calls
        upsert_calls += 1
        return await real_upsert(self, conn, snapshot, expected_seq=expected_seq)

    ports.snapshots.upsert = _counting_upsert.__get__(ports.snapshots, PostgresSnapshotRepository)

    # REPLAY -- 같은 order_id+fill_seq를 그대로 재전송
    replayed = await _record(pool, ports, sell_command)

    assert upsert_calls == 0, "REPLAY 경로는 스냅샷 upsert를 다시 호출하면 안 된다"
    assert replayed.realized_pnl_base == first.realized_pnl_base
    assert replayed.quantity == first.quantity
    assert replayed.last_journal_seq == first.last_journal_seq

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_count = await conn.fetchval(
            "SELECT count(*) FROM pos_snapshot WHERE position_key = $1", position_key
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", order_id
        )
        snapshot_row = await conn.fetchrow(
            "SELECT realized_pnl_base FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 2, "REPLAY는 새 저널 행을 만들면 안 된다(신규매수 1 + 청산 1)"
    assert snapshot_count == 1, "REPLAY는 스냅샷 행을 중복 생성하면 안 된다"
    assert audit_count == 2, "REPLAY는 새 감사이벤트를 만들면 안 된다(체결 2건분만)"
    assert snapshot_row["realized_pnl_base"] == (Decimal("120") - Decimal("100")) * Decimal("4")
