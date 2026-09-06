"""`save_equity_baseline`의 §5 조건부 UPDATE 통합테스트 — 실제 TEST_DATABASE_URL 대상.

R-30(task-1220) 추가분. SQL 자체가 검증 대상이라 asyncpg로 붙어 105번 §4.2
형태 B 스타일("직접 주입" — 이전 트랜잭션의 결과를 먼저 커밋해 두고 그 위에
다음 호출을 얹어 최종 행 상태만 확인) negative test로 검증한다.

순수 메모리 로직(ExecutionEquityTracker.seed/record 등)은
tests/unit/services/test_equity_tracker.py 쪽에 있다(task-1615, PLT-36 —
tests/unit 아래는 실DB에 접속하지 않는다).
"""
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.execution_loop.equity_tracker import save_equity_baseline
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _create_execution(pool: asyncpg.Pool, user_id) -> int:
    strategy_id = f"equity-tracker-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


async def test_save_equity_baseline_peak_never_regresses(pool) -> None:
    """105 §4.2 형태 B: 먼저 커밋된 트랜잭션이 더 높은 peak를 쓴 뒤,
    (동시에 진행 중이던) 다른 트랜잭션이 더 낮은 peak·다른 day_start_value로
    저장을 시도해도 GREATEST/CASE 때문에 DB 값이 역행하지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    today = date(2026, 9, 4)

    await save_equity_baseline(
        pool, execution_id, day_start_date=today,
        day_start_value=Decimal("1000"), peak_value=Decimal("1000"),
    )
    await save_equity_baseline(
        pool, execution_id, day_start_date=today,
        day_start_value=Decimal("1000"), peak_value=Decimal("1100"),
    )

    # 뒤늦게 커밋되는 다른 tick이 자신이 관측한(더 낮은) peak·다른
    # day_start_value로 저장을 시도한다 — 이전 read-modify-write
    # 구현이면 peak가 1100 → 900으로 역행하고 day_start도 덮였다.
    await save_equity_baseline(
        pool, execution_id, day_start_date=today,
        day_start_value=Decimal("777"), peak_value=Decimal("900"),
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_peak_value, equity_day_start_value FROM strategy_executions "
            "WHERE id = $1",
            execution_id,
        )
    assert row["equity_peak_value"] == Decimal("1100")
    assert row["equity_day_start_value"] == Decimal("1000")


async def test_save_equity_baseline_day_rollover_resets_day_start(pool) -> None:
    """날짜가 실제로 바뀌면(equity_day_start_date IS DISTINCT FROM $2) 새
    day_start_value가 반영된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    await save_equity_baseline(
        pool, execution_id, day_start_date=date(2026, 9, 3),
        day_start_value=Decimal("500"), peak_value=Decimal("500"),
    )
    await save_equity_baseline(
        pool, execution_id, day_start_date=date(2026, 9, 4),
        day_start_value=Decimal("620"), peak_value=Decimal("620"),
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value FROM strategy_executions "
            "WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_date"] == date(2026, 9, 4)
    assert row["equity_day_start_value"] == Decimal("620")


async def test_save_equity_baseline_missing_execution_fails_closed(pool) -> None:
    with pytest.raises(LookupError):
        await save_equity_baseline(
            pool, -1, day_start_date=date(2026, 9, 4),
            day_start_value=Decimal("1"), peak_value=Decimal("1"),
        )
