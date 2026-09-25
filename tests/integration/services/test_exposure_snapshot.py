"""R-27 exposure_snapshot.py 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.

DEEPEN(task-2828, docs/audit/DEPTH_R_EO.md leaf 1336): 단일왕복 성능단언 1건
(test_single_round_trip)과 negative 2건뿐이던 D1 상태에 negative를 3건 이상
(닫힌 포지션·0수량 필터도 실DB로 실증)으로, 실패 주입(커넥션 오류 비삼킴)과
게이트 적색 재현(user_id 격리 술어가 빠지면 이 스위트가 실제로 RED가 됨을
증명한 뒤 원상복구)을 신규로 채워 D2를 완비하고, 서로 다른 테넌트의 실제
동시 조회가 격리를 유지함을 asyncio.gather로 증명해 D3 요건도 만족한다."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

import src.services.execution_loop.exposure_snapshot as exposure_snapshot_mod
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
    closed_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, quantity, average_entry_price,
                 entry_time, closed_at)
            VALUES ($1, $2, $3, $4, $5, $6, now(), $7)
            """,
            user_id,
            symbol,
            exchange,
            strategy_id,
            quantity,
            average_entry_price,
            closed_at,
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


async def test_closed_position_is_excluded_from_exposure(pool):
    """negative — §3.5 open_pos CTE의 `closed_at IS NULL` 필터가 실제로
    청산된(quantity는 남아 있되 closed_at이 찍힌) 포지션을 노출 합계에서
    빼는지 실DB로 증명한다(09번 §9.1 #9: 청산 시 행을 지우지 않고
    closed_at만 채우므로, 이 필터가 없으면 청산 포지션이 살아있는
    노출로 이중 집계된다)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-closed"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("2"),
        average_entry_price=Decimal("40000"),
    )
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("5"),
        average_entry_price=Decimal("40000"),
        closed_at=datetime.now(timezone.utc),
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

    assert snapshot.position_quantity == Decimal("2")
    assert snapshot.open_positions_count == 1
    assert snapshot.gross_tenant == Decimal("2") * Decimal("50000")


async def test_zero_quantity_position_is_excluded_from_exposure(pool):
    """negative — §3.5 open_pos CTE의 `quantity <> 0` 필터 실증. 청산 직전
    잔여 0수량 행이 남아 있어도 노출·건수에 잡히면 안 된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-zero-qty"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="ETH/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("0"),
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

    assert snapshot.open_positions_count == 0
    assert snapshot.gross_tenant == Decimal("0")
    assert snapshot.gross_asset_class == {}


async def test_connection_failure_propagates_instead_of_being_swallowed(pool, monkeypatch):
    """실패 주입 — DB 커넥션 오류가 나면 예외를 삼키지 않고 그대로
    전파해야 한다. 호출부(risk_inputs_assembler.assemble_risk_inputs)가
    실패를 "노출 0"으로 오인하면 사전 리스크 게이트가 실제 포지션을 못 본
    채 통과시키는 fail-open이 된다(I-06 위반)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await load_exposure_snapshot(
                conn,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                strategy_id="strat-fail",
                provider="bitget",
                prices={"BTC/USDT": Decimal("50000")},
            )


async def test_concurrent_snapshot_reads_for_different_tenants_stay_isolated(pool):
    """다중 인스턴스 경합(D3) — 서로 다른 커넥션 위에서 두 테넌트가 실제로
    겹쳐서(asyncio.gather) 노출 스냅샷을 조회해도, 조건절 파라미터 바인딩이
    커넥션 간에 뒤섞이거나 캐시되지 않고 각자 자기 테넌트 값만 본다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await _create_execution(pool, user_a, exchange="bitget")
    execution_b = await _create_execution(pool, user_b, exchange="bitget")
    strategy_id = "strat-concurrent"

    await _insert_position(
        pool,
        user_id=user_a,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("10000"),
    )
    await _insert_position(
        pool,
        user_id=user_b,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("777"),
        average_entry_price=Decimal("10000"),
    )

    async def _load(user_id: UUID, execution_id: int) -> object:
        async with pool.acquire() as conn:
            return await load_exposure_snapshot(
                conn,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                strategy_id=strategy_id,
                provider="bitget",
                prices={"BTC/USDT": Decimal("10000")},
            )

    snapshot_a, snapshot_b = await asyncio.gather(
        _load(user_a, execution_a), _load(user_b, execution_b)
    )

    assert snapshot_a.position_quantity == Decimal("1")
    assert snapshot_b.position_quantity == Decimal("777")


async def test_regression_confirms_suite_would_catch_missing_tenant_filter(pool, monkeypatch):
    """게이트 적색 재현 — SQL의 `p.user_id = $1` 술어가 실수로 빠지면(다른
    파라미터 자리로 밀리거나 리팩터링 중 삭제되는 배선 결함) 이 스위트가
    실제로 실패(RED)함을 먼저 증명한 뒤, 원래 SQL로 되돌려 통과(GREEN)를
    재확인한다 — "테스트가 있다"가 아니라 "테스트가 이 결함을 실제로
    잡는다"를 증명한다."""
    # 심볼을 매 실행마다 새로 만든다 — 이 공유 통합 테스트 DB는 함수 간
    # 롤백이 없어(다른 케이스들도 고정 심볼을 재사용) 고정 심볼을 쓰면
    # 이전 테스트/실행에서 남은 행까지 섞여 아래 등식 단언이 깨진다.
    symbol = f"GATERED-{uuid.uuid4().hex[:8]}/USDT"
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await _create_execution(pool, user_a, exchange="bitget")
    strategy_id = "strat-gate-red"

    await _insert_position(
        pool,
        user_id=user_a,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("10"),
        average_entry_price=Decimal("100"),
    )
    await _insert_position(
        pool,
        user_id=user_b,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("999"),
        average_entry_price=Decimal("100"),
    )

    original_sql = exposure_snapshot_mod._SQL
    assert "p.user_id = $1" in original_sql
    # "$1::uuid IS NOT NULL"로 바꿔 파라미터 타입 추론은 유지하면서(항상
    # 참이 되어) 실제 테넌트 필터링만 무력화한다 — 단순 "TRUE" 치환은
    # $1이 쿼리에서 안 쓰이게 되어 asyncpg가 타입을 추론하지 못해 다른
    # 예외(IndeterminateDatatypeError)로 죽으므로 실제 배선 결함과
    # 다른 실패 모드를 재현하게 된다.
    broken_sql = original_sql.replace("p.user_id = $1", "$1::uuid IS NOT NULL")
    monkeypatch.setattr(exposure_snapshot_mod, "_SQL", broken_sql)

    async with pool.acquire() as conn:
        broken_snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_a,
            execution_id=execution_a,
            symbol=symbol,
            strategy_id=strategy_id,
            provider="bitget",
            prices={symbol: Decimal("100")},
        )
    # 결함 주입 상태 — user_b의 포지션까지 새어 들어온다(RED 재현).
    assert broken_snapshot.position_quantity == Decimal("10") + Decimal("999")
    monkeypatch.undo()

    async with pool.acquire() as conn:
        fixed_snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_a,
            execution_id=execution_a,
            symbol=symbol,
            strategy_id=strategy_id,
            provider="bitget",
            prices={symbol: Decimal("100")},
        )
    # 원상복구 후: 실제 구현은 user_a만 본다(GREEN 재확인).
    assert fixed_snapshot.position_quantity == Decimal("10")
