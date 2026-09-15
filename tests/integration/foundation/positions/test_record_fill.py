"""LB-11 `record_fill` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.2, §9.3 LB-11.
DoD(task-412): "record_fill 전 케이스(신규·추가매수·부분청산·전량청산·역방향)
+ 감사이벤트 1:1 + 감사 실패 주입 시 저널·스냅샷 롤백".
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    IdempotencyDigestMismatchError,
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
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


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _command(
    *,
    tenant_id,
    account_id,
    position_key,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal = Decimal("100"),
    fee: Money | None = None,
    order_id=None,
    fill_seq: int = 1,
    occurred_at: datetime = _OCCURRED_AT,
) -> RecordFillCommand:
    return RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=order_id or uuid4(),
        fill_seq=fill_seq,
        side=side,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.KRW),
        fee=fee,
        occurred_at=occurred_at,
        trace_id=uuid4(),
    )


async def _open(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _record(pool, ports, command, *, audit=None):
    async with pool.acquire() as conn, conn.transaction():
        return await record_fill(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=audit or ports.audit,
            clock=_clock,
        )


async def test_new_position_first_buy_opens_lot(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
    )

    snapshot = await _record(pool, ports, command)

    assert snapshot.quantity == Decimal("10")
    assert snapshot.avg_cost.amount == Decimal("100")
    assert snapshot.realized_pnl_base == Decimal("0")
    assert snapshot.last_journal_seq == 1


async def test_additional_buy_blends_average_cost(pool, ports):
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

    snapshot = await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.BUY,
            quantity=Decimal("5"),
            price=Decimal("110"),
            order_id=order_id,
            fill_seq=2,
        ),
    )

    assert snapshot.quantity == Decimal("15")
    # FIFO 로트 2개(10@100, 5@110) 위의 평단 = (1000+550)/15. NUMERIC(30,10)
    # 컬럼을 거쳐 나오므로 §3.4 규약대로 소수 10자리로 quantize해 비교한다.
    expected_avg_cost = ((Decimal("1000") + Decimal("550")) / Decimal("15")).quantize(
        Decimal("1e-10"), rounding=ROUND_HALF_EVEN
    )
    assert snapshot.avg_cost.amount == expected_avg_cost
    assert snapshot.realized_pnl_base == Decimal("0")
    assert snapshot.last_journal_seq == 2


async def test_partial_close_realizes_pnl_and_keeps_remainder(pool, ports):
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

    snapshot = await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.SELL,
            quantity=Decimal("4"),
            price=Decimal("120"),
            order_id=order_id,
            fill_seq=2,
        ),
    )

    assert snapshot.quantity == Decimal("6")
    assert snapshot.realized_pnl_base == (Decimal("120") - Decimal("100")) * Decimal("4")
    assert snapshot.last_journal_seq == 2


async def test_full_close_zeroes_quantity_and_lots(pool, ports):
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

    snapshot = await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.SELL,
            quantity=Decimal("10"),
            price=Decimal("120"),
            order_id=order_id,
            fill_seq=2,
        ),
    )

    assert snapshot.quantity == Decimal("0")
    assert snapshot.lots == []
    assert snapshot.realized_pnl_base == (Decimal("120") - Decimal("100")) * Decimal("10")
    assert snapshot.last_journal_seq == 2


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


async def test_numeric_round_trip_at_scale_boundary_quantity_and_pnl(pool, ports):
    """수치 정밀도 단언 -- DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md
    #412)가 원 리프(commit 09d8163)에 Decimal round-trip 증빙이 없다고
    지적했다. NUMERIC(30,10) 컬럼 경계에 가까운 10자리 소수 quantity/price로
    체결 2건(신규매수 -> 부분청산)을 기록하고, record_fill이 돌려준 값과
    **별도 SELECT로 재조회한 DB 저장값**이 정확히 일치하는지 확인한다 --
    INSERT...RETURNING 값만 보면 DB가 실제로 무엇을 저장했는지(캐스팅·반올림
    유실 여부)는 증명하지 못한다(test_postgres_snapshot_repository.py의 동일
    기법, task-2945/#375 DEEPEN 선례).

    지연(latency)/순차 DB 왕복 수 단언은 record_fill() 전체를 직접 재는
    test_perf_journal_append.py(LB-18, §7 B/§8.4 측정 지점이 record_fill로
    명시됨)가 이미 담당한다 -- #375(LB-9) DEEPEN이 같은 이유로 perf 축을
    별도 리프 소관으로 제외한 것과 동일하게, 이 리프에서 중복 측정하지
    않는다."""
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    buy_quantity = Decimal("0.1234567891")
    buy_price = Decimal("99999.9876543211")
    await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.BUY,
            quantity=buy_quantity,
            price=buy_price,
            order_id=order_id,
            fill_seq=1,
        ),
    )

    sell_quantity = Decimal("0.0765432109")
    sell_price = Decimal("100001.1123456789")
    snapshot = await _record(
        pool,
        ports,
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.SELL,
            quantity=sell_quantity,
            price=sell_price,
            order_id=order_id,
            fill_seq=2,
        ),
    )

    expected_quantity = (buy_quantity - sell_quantity).quantize(
        Decimal("1e-10"), rounding=ROUND_HALF_EVEN
    )
    expected_realized = ((sell_price - buy_price) * sell_quantity).quantize(
        Decimal("1e-10"), rounding=ROUND_HALF_EVEN
    )
    assert snapshot.quantity == expected_quantity
    assert snapshot.realized_pnl_base == expected_realized
    assert snapshot.avg_cost.amount == buy_price

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT quantity, avg_cost, realized_pnl_base FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert row["quantity"] == expected_quantity
    assert row["avg_cost"] == buy_price
    assert row["realized_pnl_base"] == expected_realized


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
