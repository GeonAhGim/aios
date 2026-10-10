"""LB-11 체결 수량·원가·실현손익 통합 검증."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from uuid import uuid4

from src.data.models.trading import OrderSide
from tests.integration.foundation.positions.record_fill_fixtures import (
    _command,
    _open,
    _record,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    ports as ports,
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
