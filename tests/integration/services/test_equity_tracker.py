"""`save_equity_baseline`의 §5 조건부 UPDATE 통합테스트 — 실제 TEST_DATABASE_URL 대상.

R-30(task-1220) 추가분. SQL 자체가 검증 대상이라 asyncpg로 붙어 105번 §4.2
형태 B 스타일("직접 주입" — 이전 트랜잭션의 결과를 먼저 커밋해 두고 그 위에
다음 호출을 얹어 최종 행 상태만 확인) negative test로 검증한다.

순수 메모리 로직(ExecutionEquityTracker.seed/record 등)은
tests/unit/services/test_equity_tracker.py 쪽에 있다(task-1615, PLT-36 —
tests/unit 아래는 실DB에 접속하지 않는다).
"""
import asyncio
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
    # max_size=6: 아래 동시경합 테스트가 5개 트랜잭션을 asyncio.gather로
    # 실제로 겹쳐서(대기열에서 순서대로가 아니라) 실행하려면 커넥션 풀이
    # 그만큼 여유가 있어야 진짜 DB 레벨 경합이 성립한다.
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=6)
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


async def test_save_equity_baseline_concurrent_writes_never_lose_the_max_peak(pool) -> None:
    """실동시경합(다중 트랜잭션) — 이전 증거는 `await`를 하나씩 순차로 걸어
    "먼저 커밋된 트랜잭션"을 흉내만 냈을 뿐 실제로 겹쳐 실행되지 않았다.
    여기서는 `asyncio.gather`로 서로 다른 커넥션 위에서 5개의 UPDATE를
    진짜로 동시에 날린다(105 §4.2 형태 A 스타일 다중 인스턴스 경합).
    조건부 UPDATE는 단일 원자적 SQL문이라 Postgres 행잠금으로 직렬화되지만,
    어떤 순서로 실제 실행되든 최종 peak는 시도된 값들의 최댓값이어야
    한다 — 하나라도 lost update가 나면 실패한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    today = date(2026, 9, 6)
    peaks = [Decimal("1000"), Decimal("2500"), Decimal("800"), Decimal("3000"), Decimal("1200")]

    await asyncio.gather(
        *[
            save_equity_baseline(
                pool, execution_id, day_start_date=today,
                day_start_value=Decimal("1000"), peak_value=p,
            )
            for p in peaks
        ]
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_peak_value FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row["equity_peak_value"] == max(peaks)


async def test_save_equity_baseline_propagates_connection_failure_fail_closed(
    pool, monkeypatch
) -> None:
    """실패 주입 — DB 커넥션 오류가 나면 예외를 삼키지 않고 그대로
    전파해야 한다(호출부 `record_and_persist_equity`가 이미 메모리에
    반영한 peak를 DB 저장 성공으로 착각해 그다음 write-through를
    건너뛰면 안 됨)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await save_equity_baseline(
            pool, execution_id, day_start_date=date(2026, 9, 7),
            day_start_value=Decimal("500"), peak_value=Decimal("500"),
        )


async def test_save_equity_baseline_single_round_trip(pool, monkeypatch) -> None:
    """성능 단언 — R-57의 flakiness 회피 선례를 따라 벽시계 시간 대신
    쿼리 왕복횟수를 단언한다: 조건부 UPDATE 한 번은 정확히 1회의
    `fetchrow` 호출로 끝나야 한다(숨은 read-modify-write 왕복 추가 금지)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    call_count = 0
    original_fetchrow = asyncpg.Connection.fetchrow

    async def counting_fetchrow(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", counting_fetchrow)

    await save_equity_baseline(
        pool, execution_id, day_start_date=date(2026, 9, 8),
        day_start_value=Decimal("100"), peak_value=Decimal("100"),
    )

    assert call_count == 1
