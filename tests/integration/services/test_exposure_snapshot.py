"""R-27 exposure_snapshot.py 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.execution_loop.exposure_snapshot import load_exposure_snapshot
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글턴 행 — 다른 테스트 파일의 잔여 상태를
        # 격리한다(test_execution_tick.py와 동일 관례).
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


async def _create_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str) -> int:
    strategy_id = f"exposure-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', 1000, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
        )
    assert row is not None
    return row["id"]


async def _insert_position(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    symbol: str,
    exchange: str,
    strategy_id: str,
    quantity: Decimal,
    average_entry_price: Decimal,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, quantity, average_entry_price, entry_time)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            """,
            user_id,
            symbol,
            exchange,
            strategy_id,
            quantity,
            average_entry_price,
        )


async def _insert_order(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    execution_id: int,
    symbol: str,
    exchange: str,
    strategy_id: str,
    created_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders
                (user_id, client_order_id, strategy_id, strategy_version, symbol, exchange,
                 side, order_type, quantity, execution_id, created_at)
            VALUES ($1, $2, $3, '1.0.0', $4, $5, 'BUY', 'MARKET', 1, $6, COALESCE($7, now()))
            """,
            user_id,
            f"order-{uuid.uuid4().hex}",
            strategy_id,
            symbol,
            exchange,
            execution_id,
            created_at,
        )


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
