"""LB-11 체결 수량·원가·실현손익 통합 검증."""

from __future__ import annotations

import asyncio
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.application.record_fill import UnknownPositionError
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from tests.integration.foundation.positions.record_fill_fixtures import (
    _BoomAuditAppender,
    _command,
    _key,
    _open,
    _record,
)
from tests.integration.foundation.positions.record_fill_fixtures import (
    ports as ports,
)
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

# New perf budget (first-buy record_fill, best-of-5 wall). Measured locally: MEASURED_MS;
# BUDGET_MS leaves >=30x headroom.
MEASURED_MS = 8.0  # best-of-5 wall incl. _open, local PG, 2026-11-10
BUDGET_MS = 500.0  # ~60x measured


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


# --- DEEPEN-DEPTH LB-11 (task-23141): negative / failure injection / perf / adversarial ---


async def _journal_count(pool, position_key) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )


async def test_first_buy_unknown_position_rejected_without_journal(pool, ports):
    """Negative 1: a fill for a never-opened position_key is fail-closed."""
    tenant_id, account_id, _ = await _open(pool)
    ghost_key = _key(tenant_id)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=ghost_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
    )
    with pytest.raises(UnknownPositionError):
        await _record(pool, ports, command)
    assert await _journal_count(pool, ghost_key) == 0


async def test_first_buy_wrong_account_rejected(pool, ports):
    """Negative 2: the position exists but belongs to another account."""
    tenant_id, _, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=uuid4(),
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
    )
    with pytest.raises(UnknownPositionError):
        await _record(pool, ports, command)
    assert await _journal_count(pool, position_key) == 0


async def test_first_buy_malformed_position_key_rejected(pool, ports):
    """Negative 3: a hand-assembled (non-PositionKey) key never reaches the journal."""
    tenant_id, account_id, _ = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key="not|a|valid|key",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
    )
    with pytest.raises(ValueError):
        await _record(pool, ports, command)


async def test_first_buy_audit_failure_rolls_back_journal_and_snapshot(pool, ports):
    """Failure injection + gate-red: with the audit appender failing, the first buy
    leaves no journal row and the snapshot stays empty (the first-buy assertions in
    test_new_position_first_buy_opens_lot would be red on any leaked state)."""
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
    )
    with pytest.raises(RuntimeError, match="injected audit failure"):
        await _record(pool, ports, command, audit=_BoomAuditAppender())
    assert await _journal_count(pool, position_key) == 0
    async with pool.acquire() as conn:
        snap = await ports.snapshots.get(conn, tenant_id, position_key)
    assert snap is not None and snap.quantity == Decimal("0") and snap.last_journal_seq == 0


async def test_first_buy_replay_is_idempotent(pool, ports):
    """Adversarial replay: resending the same (order_id, fill_seq) must not double the lot."""
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_id=order_id,
        fill_seq=1,
    )
    await _record(pool, ports, command)
    replayed = await _record(pool, ports, command)
    assert replayed.quantity == Decimal("10")
    assert replayed.last_journal_seq == 1
    assert await _journal_count(pool, position_key) == 1


async def test_concurrent_first_buys_serialize_to_exact_total(pool, ports):
    """Concurrency: 5 simultaneous first-buy fills on one position sum exactly (lock)."""
    tenant_id, account_id, position_key = await _open(pool)
    commands = [
        _command(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            side=OrderSide.BUY,
            quantity=Decimal("2"),
            order_id=uuid4(),
            fill_seq=1,
        )
        for _ in range(5)
    ]
    await asyncio.gather(*(_record(pool, ports, c) for c in commands))
    async with pool.acquire() as conn:
        snap = await ports.snapshots.get(conn, tenant_id, position_key)
    assert snap.quantity == Decimal("10")
    assert snap.last_journal_seq == 5


@pytest.mark.perf
async def test_first_buy_perf_budget_and_10x_injection_gate_red(pool, ports, perf_budget):
    """Perf: best-of-5 wall latency of a first-buy record_fill. Measured MEASURED_MS locally;
    BUDGET_MS leaves >=30x headroom. A virtual 10x-budget delay on journal.append must
    turn the same assertion red."""

    async def _one():
        tenant_id, account_id, position_key = await _open(pool)
        return await _record(
            pool,
            ports,
            _command(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                side=OrderSide.BUY,
                quantity=Decimal("10"),
            ),
        )

    reset_virtual_totals()
    sample = await perf_budget.best_of_async(_one, n=5, warmup=1)
    assert sample.wall_ms < BUDGET_MS, perf_budget.describe(sample, budget_ms=BUDGET_MS)

    original = PostgresJournalRepository.append
    PostgresJournalRepository.append = virtual_delayed_async(original, BUDGET_MS * 10 / 1000)
    try:
        reset_virtual_totals()
        slow = await perf_budget.best_of_async(_one, n=3)
    finally:
        PostgresJournalRepository.append = original
        reset_virtual_totals()
    assert slow.wall_ms >= BUDGET_MS


# --- DEEPEN-DEPTH LB-11 partial close (negative / failure injection / replay) ---


async def _seed_long_10(pool, ports):
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
            order_id=order_id,
            fill_seq=1,
        ),
    )
    return tenant_id, account_id, position_key, order_id


def _partial_sell(tenant_id, account_id, position_key, order_id, quantity, fill_seq=2):
    return _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=quantity,
        price=Decimal("120"),
        order_id=order_id,
        fill_seq=fill_seq,
    )


async def test_partial_close_oversell_of_remainder_rejected(pool, ports):
    """Negative 1: after a partial close (10 -> 6), selling 7 is an over-sell and is rejected."""
    tenant_id, account_id, position_key, order_id = await _seed_long_10(pool, ports)
    await _record(
        pool, ports, _partial_sell(tenant_id, account_id, position_key, order_id, Decimal("4"))
    )
    with pytest.raises(NegativeQuantityError):
        await _record(
            pool,
            ports,
            _partial_sell(tenant_id, account_id, position_key, order_id, Decimal("7"), fill_seq=3),
        )
    assert await _journal_count(pool, position_key) == 2
    async with pool.acquire() as conn:
        snap = await ports.snapshots.get(conn, tenant_id, position_key)
    assert snap.quantity == Decimal("6") and snap.last_journal_seq == 2


async def test_partial_close_on_empty_position_rejected(pool, ports):
    """Negative 2: a sell on a freshly opened (no lots) position is rejected."""
    tenant_id, account_id, position_key = await _open(pool)
    with pytest.raises(NegativeQuantityError):
        await _record(
            pool,
            ports,
            _partial_sell(tenant_id, account_id, position_key, uuid4(), Decimal("4")),
        )
    assert await _journal_count(pool, position_key) == 0


async def test_partial_close_wrong_account_rejected(pool, ports):
    """Negative 3: a partial close from another account is UnknownPositionError."""
    tenant_id, _, position_key, order_id = await _seed_long_10(pool, ports)
    with pytest.raises(UnknownPositionError):
        await _record(
            pool,
            ports,
            _partial_sell(tenant_id, uuid4(), position_key, order_id, Decimal("4")),
        )
    assert await _journal_count(pool, position_key) == 1
    async with pool.acquire() as conn:
        snap = await ports.snapshots.get(conn, tenant_id, position_key)
    assert snap.quantity == Decimal("10") and snap.realized_pnl_base == Decimal("0")


async def test_partial_close_audit_failure_rolls_back_remainder(pool, ports):
    """Failure injection: audit failure on a partial close keeps the 10-lot snapshot."""
    tenant_id, account_id, position_key, order_id = await _seed_long_10(pool, ports)
    command = _partial_sell(tenant_id, account_id, position_key, order_id, Decimal("4"))
    with pytest.raises(RuntimeError, match="injected audit failure"):
        await _record(pool, ports, command, audit=_BoomAuditAppender())
    assert await _journal_count(pool, position_key) == 1
    async with pool.acquire() as conn:
        snap = await ports.snapshots.get(conn, tenant_id, position_key)
    assert snap.quantity == Decimal("10") and snap.last_journal_seq == 1
    assert snap.realized_pnl_base == Decimal("0")


async def test_partial_close_replay_keeps_remainder_and_pnl(pool, ports):
    """Adversarial replay: a resent partial close neither re-closes lots nor re-realizes PnL."""
    tenant_id, account_id, position_key, order_id = await _seed_long_10(pool, ports)
    command = _partial_sell(tenant_id, account_id, position_key, order_id, Decimal("4"))
    first = await _record(pool, ports, command)
    replayed = await _record(pool, ports, command)
    assert replayed.quantity == Decimal("6") == first.quantity
    assert replayed.realized_pnl_base == first.realized_pnl_base == Decimal("80")
    assert await _journal_count(pool, position_key) == 2
