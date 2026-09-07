"""LA-3 — 알려진 venue의 세션 스펙 상수(KRX·US·크립토).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-3.
개장·마감 시각은 각 거래소 정규시장 공지 기준 표준 사실이다(휴장일 목록은
여기 포함하지 않는다 — 그 목록은 R4 미확인 대상이며 LA-12 yaml 로더가 별도
공급한다). NYSE·NASDAQ은 정규장 시간이 같아 `KIS_US` 하나로 취급한다.

KIS_KRX의 `close_time=15:30`은 연속경쟁매매(09:00~15:20)와 그 뒤에 이어지는
종가단일가매매(15:20~15:30, 당일 종가를 단일가로 결정하는 호가 집중 구간)를
모두 포함한 정규장 마감 시각이다 — 유가증권시장 업무규정(한국거래소)상
정규시장은 이 종가단일가매매 구간까지가 하나의 정규 세션이므로, 개장·폐장
여부 판정(`VenueCalendar.is_open`)에는 별도 세션 구간으로 쪼개지 않는다.
(참고: https://easylaw.go.kr/CSP/CnpClsMain.laf?csmSeq=1701 — "매매거래일·
거래시간 및 거래 원칙 등")
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
