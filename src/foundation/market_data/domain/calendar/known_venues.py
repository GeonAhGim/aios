"""LA-3 — 알려진 venue의 세션 스펙 상수(KRX·US·크립토).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-3.
개장·마감 시각은 각 거래소 정규시장 공지 기준 표준 사실이다(휴장일 목록은
여기 포함하지 않는다 — 그 목록은 R4 미확인 대상이며 LA-12 yaml 로더가 별도
공급한다). NYSE·NASDAQ은 정규장 시간이 같아 `KIS_US` 하나로 취급한다.
"""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.calendar.session_rules import SessionSpec

_WEEKDAYS_MON_FRI = frozenset({0, 1, 2, 3, 4})

KNOWN_SESSIONS: dict[str, SessionSpec] = {
    Venue.KIS_KRX.value: SessionSpec(
        tz=ZoneInfo("Asia/Seoul"),
        open_time=time(9, 0),
        close_time=time(15, 30),
        weekdays=_WEEKDAYS_MON_FRI,
    ),
    Venue.KIS_US.value: SessionSpec(
        tz=ZoneInfo("America/New_York"),
        open_time=time(9, 30),
        close_time=time(16, 0),
        weekdays=_WEEKDAYS_MON_FRI,
    ),
    Venue.BITGET.value: SessionSpec(
        tz=ZoneInfo("UTC"),
        open_time=time.min,
        close_time=time.min,
        weekdays=frozenset(range(7)),
        continuous=True,
    ),
}
