"""U-15 일일 리포트 — 손익·체결·위반 요약.

미검증/TODO: 체결·손익 데이터 소스 연동(실제 원장·performance 모듈 조회)은
이 리프 스콥 밖이다 — 호출자가 그날의 실현손익과 체결 건수를 직접 넘긴다.
위반 건수만 이 모듈이 상태 저장소에서 직접 조회한다(단일 출처).
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
