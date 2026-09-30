"""DEEPEN(task-9376) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수 5828 qa-2)가 패키지 마커만 남기고 비워
둔 파일이다. 이 디렉터리의 `test_equity_tracker.py`는 `save_equity_baseline`
(105번 §4.2 형태 B 조건부 UPDATE) 자체만 실DB로 검증하고, 그 위에서
seed/record/저장을 한 트랜잭션 경계로 묶는 오케스트레이션 함수
`record_and_persist_equity`는 mock pool을 쓰는
`tests/unit/services/test_equity_tracker.py`에서만 검증된다(PLT-36,
tests/unit 아래는 실DB 접속 금지). 여기서는 그 함수를 실DB로만 확인할 수
있는 세 공백만 메운다:

1. 존재하지 않는 execution_id로 전체 오케스트레이션을 호출하면
   `save_equity_baseline`의 `LookupError`가 seed/record 단계를 거치고도
   삼켜지지 않고 그대로 전파되는지 — mock 테스트는 DB 자체가 없어 이
   실제 UPDATE 0-row 경로를 검증하지 못한다.
2. 하루 경계가 실제로 바뀌어도(day_start reset) `GREATEST` 기반 peak는
   역행하지 않는지 — `test_equity_tracker.py`의 day-rollover 테스트는
   day_start_value만 확인하고 peak는 항상 day_start와 동일한 값만 써서
   이 상호작용을 실측하지 않는다.
3. day_start_equity가 0인 극단 입력에서 `ZeroDivisionError` 대신 0%로
   fail-safe 처리되는지 실DB 왕복까지 포함해 확인한다(순수 로직 자체는
   unit에 있지만, 그 결과가 실제로 DB에 그대로 저장되는지는 여기서만
   확인 가능).

실패주입은 `save_equity_baseline`의 UPDATE 경로 실패는 이미
`test_equity_tracker.py`에서 다루므로, 여기서는 seed 단계의 SELECT 경로
(`load_equity_baseline`)가 커넥션 오류를 삼키지 않고 전파하는지를 다룬다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from src.services.execution_loop.equity_tracker import (
    ExecutionEquityTracker,
    record_and_persist_equity,
)
from tests.integration.conftest import create_test_user
from tests.integration.services.test_equity_tracker import _asyncpg_dsn, _create_execution


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_record_and_persist_equity_propagates_missing_execution_via_real_db(pool) -> None:
    """`record_and_persist_equity`가 존재하지 않는 execution_id에 대해
    `save_equity_baseline`의 `LookupError`를 삼키지 않고 그대로 호출자에게
    전파해야 한다 -- seed 단계(SELECT, 0-row -> None)를 먼저 거치고도
    저장 단계에서 실패가 조용히 사라지면 안 된다."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 9))

    with pytest.raises(LookupError):
        await record_and_persist_equity(pool, tracker, -1, Decimal("1000"))


async def test_record_and_persist_equity_peak_survives_real_day_rollover(pool) -> None:
    """day_start_date가 실제로 바뀌어도(day_start_value 초기화) 이미
    저장된 더 높은 peak는 `GREATEST`로 역행하지 않아야 한다 --
    `test_equity_tracker.py::test_save_equity_baseline_day_rollover_resets_day_start`
    는 day_start_value만 확인하고, 이 rollover가 peak 컬럼에 부수효과를
    일으키지 않는지는 실측하지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    day1 = date(2026, 9, 9)
    tracker1 = ExecutionEquityTracker(today=lambda: day1)
    await record_and_persist_equity(pool, tracker1, execution_id, Decimal("1000"))
    await record_and_persist_equity(pool, tracker1, execution_id, Decimal("1500"))

    # 다음 날, 새 프로세스(재시작 시나리오)가 기존 기준점을 DB에서 seed한 뒤
    # 더 낮은 equity로 하루를 시작한다.
    day2 = date(2026, 9, 10)
    tracker2 = ExecutionEquityTracker(today=lambda: day2)
    await record_and_persist_equity(pool, tracker2, execution_id, Decimal("1200"))

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_date"] == day2
    assert row["equity_day_start_value"] == Decimal("1200")
    assert row["equity_peak_value"] == Decimal("1500")


async def test_record_and_persist_equity_zero_day_start_equity_is_fail_safe_not_division_error(
    pool,
) -> None:
    """day_start_equity가 0인 극단 입력(정상적으로는 상위 계층이 막아야
    하지만, `record()`는 방어적으로 0%를 반환한다)에서도 예외 없이
    실DB round-trip이 끝나고, 저장된 baseline이 실제로 0인지 확인한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 11))

    daily_pnl_pct, drawdown_pct = await record_and_persist_equity(
        pool, tracker, execution_id, Decimal("0")
    )

    assert daily_pnl_pct == Decimal("0")
    assert drawdown_pct == Decimal("0")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_value, equity_peak_value FROM strategy_executions "
            "WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_value"] == Decimal("0")
    assert row["equity_peak_value"] == Decimal("0")


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_record_and_persist_equity_seed_select_failure_propagates_fail_closed(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test_equity_tracker.py`의 실패주입 테스트는 저장(UPDATE) 경로의
    커넥션 오류만 다룬다. 여기서는 그보다 앞선 seed 단계의 SELECT
    (`load_equity_baseline`)가 커넥션 오류를 삼키지 않고 전파하는지
    확인한다 -- 삼켜지면 tracker가 잘못된(미복구) 기준점으로 이후 record()를
    진행해 재시작 직후 하루 손익 판단이 틀어진다."""
    execution_id_placeholder = 999_999

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 12))

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await record_and_persist_equity(pool, tracker, execution_id_placeholder, Decimal("1000"))

    # 실패한 seed 시도가 tracker를 "이미 seed됨"으로 잘못 표시해서는 안 된다
    # -- 다음 호출이 다시 DB 복구를 시도할 수 있어야 fail-closed다.
    assert not tracker.is_seeded(execution_id_placeholder)
