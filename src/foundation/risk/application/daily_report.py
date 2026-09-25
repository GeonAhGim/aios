"""U-15 daily report — P&L, fill, and violation summary.

Unverified/TODO: wiring to a real fill/P&L data source (the real ledger /
performance module) is out of this leaf's scope — the caller passes in
today's realized P&L and fill count directly. Only the violation count is
read by this module from the state store (single source of truth).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.foundation.risk.ports.state import PersonalOperationStatePort


@dataclass(frozen=True)
class PersonalDailyReport:
    report_date: date
    realized_pnl_krw: Decimal
    fill_count: int
    violation_count: int


async def build_daily_report(
    *,
    report_date: date,
    realized_pnl_krw: Decimal,
    fill_count: int,
    state: PersonalOperationStatePort,
) -> PersonalDailyReport:
    violation_count = await state.violation_count_on(report_date)
    return PersonalDailyReport(
        report_date=report_date,
        realized_pnl_krw=realized_pnl_krw,
        fill_count=fill_count,
        violation_count=violation_count,
    )
