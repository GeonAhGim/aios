"""R-27 exposure_snapshot.py 통합테스트 — 실 DB 대상 (기본 경로/스코프).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.

DEEPEN(task-2828, docs/audit/DEPTH_R_EO.md leaf 1336) 원본 D2/D3 증빙 중 기본
경로(단일왕복 성능단언, 6개 스코프 키 채움, 가격 폴백, 테넌트 격리, 미분류
심볼 버킷, 체결 카운트/안전필드)는 이 파일에 남고, negative/실패주입/D3
동시성/게이트 적색 재현은 test_exposure_snapshot_negative.py로 분리했다
(RATCHET-split, 500줄 경고 해소). fixture/헬퍼는
_exposure_snapshot_fixtures.py 공유 모듈로 뺐다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.services.execution_loop import exposure_snapshot as snapshot_mod
from src.services.execution_loop.exposure_snapshot import load_exposure_snapshot
from tests.integration.conftest import create_test_user
from tests.integration.services._exposure_snapshot_fixtures import (
    _create_execution,
    _insert_order,
    _insert_position,
    pool,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

__all__ = ["pool"]

# New budget: best-of-5 wall latency of one snapshot. Measured locally: MEASURED_MS;
# BUDGET_MS leaves >=30x headroom.
MEASURED_MS = 1.2  # best-of-5 wall, local PG, 2026-11-10
BUDGET_MS = 50.0  # ~40x measured


async def test_single_round_trip(pool, monkeypatch):
    """DoD (1) — 단일 쿼리: conn.fetchrow가 정확히 1번만 불려야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")

    call_count = 0
    original_fetchrow = asyncpg.Connection.fetchrow

    async def counting_fetchrow(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", counting_fetchrow)

    async with pool.acquire() as conn:
        await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="BTC/USDT",
            strategy_id="strat-1",
            provider="bitget",
            prices={"BTC/USDT": Decimal("50000")},
        )

    assert call_count == 1


async def test_six_scope_keys_filled_and_price_used(pool):
    """DoD (2) — tenant/strategy/symbol/provider/position/asset_class 전부
    채워지고, prices에 심볼이 있으면 그 가격으로 시가평가한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-scope"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("2"),
        average_entry_price=Decimal("40000"),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="BTC/USDT",
            strategy_id=strategy_id,
            provider="bitget",
            prices={"BTC/USDT": Decimal("50000")},
        )

    expected_mv = Decimal("2") * Decimal("50000")
    assert snapshot.gross_tenant == expected_mv
    assert snapshot.net_tenant == expected_mv
    assert snapshot.gross_strategy == expected_mv
    assert snapshot.gross_symbol == expected_mv
    assert snapshot.gross_provider == expected_mv
    assert snapshot.position_quantity == Decimal("2")
    assert snapshot.open_positions_count == 1
    assert snapshot.gross_asset_class == {"ASSET_CLASS:CRYPTO": expected_mv}
    assert snapshot.input_refs == ()


async def test_price_missing_falls_back_to_entry_price_and_records_input_ref(pool):
    """DoD (2) — prices에 심볼이 없으면 average_entry_price로 근사하고
    (0/NaN 대체 금지) input_refs에 'mark:entry_fallback'을 남긴다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-fallback"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="ETH/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("3"),
        average_entry_price=Decimal("2000"),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="ETH/USDT",
            strategy_id=strategy_id,
            provider="bitget",
            prices={},
        )

    expected_mv = Decimal("3") * Decimal("2000")
    assert snapshot.gross_symbol == expected_mv
    assert snapshot.input_refs == ("mark:entry_fallback",)


async def test_other_users_positions_are_excluded(pool):
    """DoD (3) — 같은 symbol로 두 user_id의 포지션을 넣고 합계가 격리됨을
    실DB로 재현한다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await _create_execution(pool, user_a, exchange="bitget")
    strategy_id = "strat-isolated"

    await _insert_position(
        pool,
        user_id=user_a,
        symbol="SOL/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("10"),
        average_entry_price=Decimal("100"),
    )
    await _insert_position(
        pool,
        user_id=user_b,
        symbol="SOL/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("999"),
        average_entry_price=Decimal("100"),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_a,
            execution_id=execution_a,
            symbol="SOL/USDT",
            strategy_id=strategy_id,
            provider="bitget",
            prices={"SOL/USDT": Decimal("100")},
        )

    assert snapshot.gross_tenant == Decimal("1000")
    assert snapshot.position_quantity == Decimal("10")
    assert snapshot.open_positions_count == 1


async def test_unknown_symbol_goes_to_unknown_asset_class_bucket(pool):
    """DoD (4) — 화이트리스트에 없는 심볼은 0으로 뭉개지 말고 UNKNOWN 버킷으로
    분리한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="krx")
    strategy_id = "strat-unknown"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="005930",
        exchange="krx",
        strategy_id=strategy_id,
        quantity=Decimal("5"),
        average_entry_price=Decimal("70000"),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="005930",
            strategy_id=strategy_id,
            provider="krx",
            prices={},
        )

    expected_mv = Decimal("5") * Decimal("70000")
    assert snapshot.gross_asset_class == {"ASSET_CLASS:UNKNOWN": expected_mv}


async def test_trades_counts_and_safety_fields_are_populated(pool):
    """DoD (3) — trades_1h/24h 카운트 및 safety 필드 채움.

    eventless orders 잔재 방지: 삽입한 order_id를 추적해 finally에서 삭제한다.
    참고: tests/integration/oms/test_gate_perf_residue_cleanup.py 패턴.
    """
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-trades"

    # Track order IDs for cleanup (orders_residue.py sentinel)
    order_ids: list = []

    try:
        order_ids.append(
            await _insert_order(
                pool,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                exchange="bitget",
                strategy_id=strategy_id,
            )
        )
        order_ids.append(
            await _insert_order(
                pool,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                exchange="bitget",
                strategy_id=strategy_id,
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )

        async with pool.acquire() as conn:
            snapshot = await load_exposure_snapshot(
                conn,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                strategy_id=strategy_id,
                provider="bitget",
                prices={"BTC/USDT": Decimal("50000")},
            )

        assert snapshot.trades_1h == 1
        assert snapshot.trades_24h == 1
        assert snapshot.cb_level == "normal"
    finally:
        # Clean up eventless orders to satisfy orders_residue.py sentinel
        if order_ids:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM orders WHERE order_id = ANY($1::uuid[])",
                    order_ids,
                )


# --- DEEPEN-DEPTH R-27 (task-23133): negative / failure injection / perf / D3 ---


async def _load(pool, user_id, execution_id, *, strategy_id, prices, symbol="BTC/USDT"):
    async with pool.acquire() as conn:
        return await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol=symbol,
            strategy_id=strategy_id,
            provider="bitget",
            prices=prices,
        )


async def test_other_strategy_position_excluded_from_strategy_scope_only(pool):
    """Negative: a sibling strategy's position counts toward the tenant total but
    never toward gross_strategy of the requested strategy."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    for sid, qty in (("strat-mine", "1"), ("strat-other", "7")):
        await _insert_position(
            pool,
            user_id=user_id,
            symbol="BTC/USDT",
            exchange="bitget",
            strategy_id=sid,
            quantity=Decimal(qty),
            average_entry_price=Decimal("100"),
        )
    snap = await _load(
        pool, user_id, execution_id, strategy_id="strat-mine", prices={"BTC/USDT": Decimal("100")}
    )
    assert snap.gross_strategy == Decimal("100")
    assert snap.gross_tenant == Decimal("800")


async def test_user_without_positions_yields_zero_exposure(pool):
    """Negative: no open positions -> zeros and an empty asset-class map, never None/NaN."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    snap = await _load(pool, user_id, execution_id, strategy_id="none", prices={})
    assert snap.gross_tenant == snap.net_tenant == snap.gross_symbol == Decimal(0)
    assert snap.open_positions_count == 0
    assert snap.gross_asset_class == {}
    assert snap.max_leverage is None


async def test_unrelated_symbol_price_does_not_mark_the_position(pool):
    """Negative: a price for another symbol must not be applied; the entry price is used."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id="s",
        quantity=Decimal("2"),
        average_entry_price=Decimal("10"),
    )
    snap = await _load(
        pool, user_id, execution_id, strategy_id="s", prices={"ETH/USDT": Decimal("999999")}
    )
    assert snap.gross_symbol == Decimal("20")
    assert snap.input_refs == ("mark:entry_fallback",)


async def test_empty_aggregate_row_raises_instead_of_fabricating_snapshot(pool, monkeypatch):
    """Failure injection: fetchrow returning None must fail closed with RuntimeError."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")

    async def _none(self, *args, **kwargs):
        return None

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _none)
    with pytest.raises(RuntimeError, match="exposure_snapshot"):
        await _load(pool, user_id, execution_id, strategy_id="s", prices={})


async def test_gate_red_dropped_prices_are_caught_by_mark_assertion(pool, monkeypatch):
    """Gate-red reproduction: with the price payload broken (dropped), the exact
    mark-to-market assertion of the happy path no longer holds."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id="s",
        quantity=Decimal("2"),
        average_entry_price=Decimal("40000"),
    )
    prices = {"BTC/USDT": Decimal("50000")}
    good = await _load(pool, user_id, execution_id, strategy_id="s", prices=prices)
    assert good.gross_tenant == Decimal("100000")
    monkeypatch.setattr(snapshot_mod, "_encode_prices", lambda _p: "{}")
    broken = await _load(pool, user_id, execution_id, strategy_id="s", prices=prices)
    assert broken.gross_tenant != Decimal("100000")


async def test_concurrent_snapshots_for_same_user_are_consistent(pool):
    """D3 concurrency: parallel reads on separate connections see identical totals
    and never leak a second tenant's rows (INVARIANTS tenant isolation)."""
    user_id = await create_test_user(pool)
    other = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    for uid, qty in ((user_id, "3"), (other, "500")):
        await _insert_position(
            pool,
            user_id=uid,
            symbol="BTC/USDT",
            exchange="bitget",
            strategy_id="s",
            quantity=Decimal(qty),
            average_entry_price=Decimal("10"),
        )
    snaps = await asyncio.gather(
        *(_load(pool, user_id, execution_id, strategy_id="s", prices={}) for _ in range(4))
    )
    assert {s.gross_tenant for s in snaps} == {Decimal("30")}


async def _timed_snapshot_ms(pool, perf_budget, user_id, execution_id) -> float:
    async def _call():
        return await _load(
            pool, user_id, execution_id, strategy_id="s", prices={"BTC/USDT": Decimal("1")}
        )

    return (await perf_budget.best_of_async(_call, n=5, warmup=1)).wall_ms


@pytest.mark.perf
async def test_snapshot_wall_latency_within_budget_and_red_under_10x_delay(
    pool, perf_budget: PerfBudget, monkeypatch
):
    """Perf: best-of-5 wall latency of the single-round-trip snapshot stays under
    BUDGET_MS; a virtual 10x-of-budget delay on fetchrow must turn the same
    assertion red."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id="s",
        quantity=Decimal("1"),
        average_entry_price=Decimal("1"),
    )
    healthy = await _timed_snapshot_ms(pool, perf_budget, user_id, execution_id)
    print(f"MEASURED_MS={healthy:.3f}")
    assert healthy < BUDGET_MS
    reset_virtual_totals()
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetchrow",
        virtual_delayed_async(asyncpg.Connection.fetchrow, BUDGET_MS * 10 / 1000),
    )
    slow = await _timed_snapshot_ms(pool, perf_budget, user_id, execution_id)
    assert slow >= BUDGET_MS * 10
    assert not slow < BUDGET_MS
