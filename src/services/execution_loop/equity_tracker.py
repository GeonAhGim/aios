"""FD-8.3 Daily Loss / Max Drawdown 지표 — 실행별 equity 추적.

Watchdog(9.1)의 `_EquityWindow`(5분 롤링 peak-to-current)와는 목적이
다르다 — 여기는 "오늘 하루" 손익률과 "실행 시작 이후 all-time peak"
낙폭이 필요해 별도로 관리한다.

PM 배정 ③(agent-platform-12, 2026-09-02) — 이전엔 프로세스 메모리에만
있어 재시작하면 유실됐다(오늘 이미 -2% 손실 중이었어도 재시작 직후
새 프로세스는 "오늘 시작"으로 착각). `seed()`로 DB에서 읽은 기존
기준점을 최초 1회만 주입하고, `load_baseline`/`save_baseline`(아래)이
strategy_executions의 equity_day_start_date/equity_day_start_value/
equity_peak_value 컬럼에 매 record() 후 write-through한다."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import asyncpg

Clock = Callable[[], date]


class ExecutionEquityTracker:
    def __init__(self, *, today: Clock = date.today) -> None:
        self._today = today
        self._day_start_equity: dict[int, tuple[date, Decimal]] = {}
        self._peak_equity: dict[int, Decimal] = {}

    def is_seeded(self, execution_id: int) -> bool:
        """이 프로세스에서 이미 이 execution을 한 번이라도 처리했는가 —
        `seed()`를 매 tick마다 DB 왕복시키지 않기 위한 최초 1회 판단용."""
        return execution_id in self._peak_equity

    def seed(
        self,
        execution_id: int,
        *,
        day_start_date: date | None,
        day_start_equity: Decimal | None,
        peak_equity: Decimal | None,
    ) -> None:
        """재시작 복구 — DB에서 읽은 기존 기준점을 메모리에 주입한다.
        이미 메모리에 값이 있으면 덮어쓰지 않는다(`is_seeded`로 호출부가
        먼저 걸러내는 게 정상 경로지만, 방어적으로 여기서도 재확인)."""
        have_baseline = day_start_date is not None and day_start_equity is not None
        if execution_id not in self._day_start_equity and have_baseline:
            self._day_start_equity[execution_id] = (day_start_date, day_start_equity)  # type: ignore[assignment]
        if execution_id not in self._peak_equity and peak_equity is not None:
            self._peak_equity[execution_id] = peak_equity

    def day_start(self, execution_id: int) -> tuple[date, Decimal]:
        """`record()`가 이미 한 번이라도 불린 뒤에만 유효 — 영속화 호출부
        (`record_and_persist_equity`)가 record() 직후에만 사용한다."""
        return self._day_start_equity[execution_id]

    def peak(self, execution_id: int) -> Decimal:
        return self._peak_equity[execution_id]

    def record(self, execution_id: int, equity: Decimal) -> tuple[Decimal, Decimal]:
        """(daily_pnl_pct, drawdown_pct) 반환 — 둘 다 이 함수를 통해서만
        갱신된다(RiskEngine은 이 값을 그대로 소비만 한다)."""
        current_day = self._today()

        day_start = self._day_start_equity.get(execution_id)
        if day_start is None or day_start[0] != current_day:
            self._day_start_equity[execution_id] = (current_day, equity)
            day_start_equity = equity
        else:
            day_start_equity = day_start[1]

        daily_pnl_pct = (
            Decimal("0")
            if day_start_equity <= 0
            else (equity - day_start_equity) / day_start_equity * 100
        )

        peak = self._peak_equity.get(execution_id, equity)
        peak = max(peak, equity)
        self._peak_equity[execution_id] = peak
        drawdown_pct = Decimal("0") if peak <= 0 else (peak - equity) / peak * 100

        return daily_pnl_pct, drawdown_pct


async def load_equity_baseline(
    pool: asyncpg.Pool, execution_id: int
) -> tuple[date | None, Decimal | None, Decimal | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    if row is None:
        return None, None, None
    return row["equity_day_start_date"], row["equity_day_start_value"], row["equity_peak_value"]


async def save_equity_baseline(
    pool: asyncpg.Pool,
    execution_id: int,
    *,
    day_start_date: date,
    day_start_value: Decimal,
    peak_value: Decimal,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET equity_day_start_date = $2, "
            "equity_day_start_value = $3, equity_peak_value = $4 WHERE id = $1",
            execution_id,
            day_start_date,
            day_start_value,
            peak_value,
        )


async def record_and_persist_equity(
    pool: asyncpg.Pool, tracker: ExecutionEquityTracker, execution_id: int, equity: Decimal
) -> tuple[Decimal, Decimal]:
    """`assemble_account_state`가 부르는 진입점 — 최초 1회 DB에서
    기준점을 복구(seed)하고, 매번 계산 후 write-through로 저장한다."""
    if not tracker.is_seeded(execution_id):
        day_start_date, day_start_value, peak_value = await load_equity_baseline(
            pool, execution_id
        )
        tracker.seed(
            execution_id,
            day_start_date=day_start_date,
            day_start_equity=day_start_value,
            peak_equity=peak_value,
        )

    daily_pnl_pct, drawdown_pct = tracker.record(execution_id, equity)

    day_start_date, day_start_value = tracker.day_start(execution_id)
    await save_equity_baseline(
        pool,
        execution_id,
        day_start_date=day_start_date,
        day_start_value=day_start_value,
        peak_value=tracker.peak(execution_id),
    )
    return daily_pnl_pct, drawdown_pct
