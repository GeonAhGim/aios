"""FD-8.3 Daily Loss / Max Drawdown 지표 — 실행별 equity 추적.

Draft — 프로세스 메모리에만 있다(재시작 시 유실). Watchdog(9.1)의
`_EquityWindow`(5분 롤링 peak-to-current)와는 목적이 다르다 — 여기는
"오늘 하루" 손익률과 "실행 시작 이후 all-time peak" 낙폭이 필요해
별도로 관리한다. 영속화(재시작 후에도 유지)는 별도 leaf 대상으로 명시.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

Clock = Callable[[], date]


class ExecutionEquityTracker:
    def __init__(self, *, today: Clock = date.today) -> None:
        self._today = today
        self._day_start_equity: dict[int, tuple[date, Decimal]] = {}
        self._peak_equity: dict[int, Decimal] = {}

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
