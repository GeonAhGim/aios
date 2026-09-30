"""R-27 exposure_snapshot.py 통합테스트 — 실 DB 대상 (기본 경로/스코프).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.

DEEPEN(task-2828, docs/audit/DEPTH_R_EO.md leaf 1336) 원본 D2/D3 증빙 중 기본
경로(단일왕복 성능단언, 6개 스코프 키 채움, 가격 폴백, 테넌트 격리, 미분류
심볼 버킷, 체결 카운트/안전필드)는 이 파일에 남고, negative/실패주입/D3
동시성/게이트 적색 재현은 test_exposure_snapshot_negative.py로 분리했다
(RATCHET-split, 500줄 경고 해소). fixture/헬퍼는
_exposure_snapshot_fixtures.py 공유 모듈로 뺐다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

from src.services.execution_loop.exposure_snapshot import load_exposure_snapshot
from tests.integration.conftest import create_test_user
from tests.integration.services._exposure_snapshot_fixtures import (
    _create_execution,
    _insert_order,
    _insert_position,
    pool,
)

__all__ = ["pool"]


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
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-trades"
    await _insert_order(
        pool,
        user_id=user_id,
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
    )
    await _insert_order(
        pool,
        user_id=user_id,
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
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
