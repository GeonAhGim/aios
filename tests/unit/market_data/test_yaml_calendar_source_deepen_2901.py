"""RD-21 `adapters/yaml_calendar_source.py` + `domain/calendar/session_rules.py`
— DEEPEN(task-2901, docs/audit/DEPTH_DC_RD.md#1768) D1 -> D2 증빙.

기존 test_yaml_calendar_source.py는 negative 3건(collected_at/source 누락
거부, 위조 휴장일 미등재)으로 §10 R4 fail-closed를 단건씩 증명했다(D1) —
소급감사(task-2726)에서 "실패주입(DB/네트워크) 없음, 성능단언 없음,
게이트적색 재현이 결정적이지 않음"으로 지적됐다(1768행). 이 파일이 그
부족분을 채운다. `load_calendar`는 파일시스템 I/O가 유일한 경계이므로(DB는
`adapters/postgres_calendar_repository.py` 소관, task-2901 files 범위 밖),
여기서 "실패주입"이란 그 경계(파일 읽기·yaml 파싱·스키마 필드 접근)에서
발생하는 예외가 조용히 삼켜지거나 빈 캘린더로 위장되지 않고 그대로
전파되는지를 증명한다. 새 기능 없음, 깊이만 올림.

1. 실패 주입 — 파일 읽기 OSError, yaml 구문 오류, early_closes 필수 키
   누락, 잘못된 시각 포맷이 각각 조용히 삼켜지지 않고 그대로 전파되는지.
2. 성능 단언 — 대량 휴장일(5,000건) 파싱과 `VenueCalendar` 대량 반복 조회가
   절대시간 예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — 실제 US_2026.yaml(NYSE 공식 공시)로 조립한
   `VenueCalendar`를 추수감사절 주간 타임라인으로 재생한다: 정규 개장일 ->
   휴장일 -> 조기폐장일 -> 주말 -> 다음 정규 개장일. 각 경계에서 이전
   단계의 개폐 상태가 다음 단계로 새지 않음을 분단위로 증명한다(§9 RD-21
   DoD "임의 날짜에 대해 개장 여부 질의가 공시 원문과 일치").
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from src.foundation.market_data.adapters.yaml_calendar_source import load_calendar
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "market_calendars"
_KRX_2026 = _CONFIG_DIR / "KRX_2026.yaml"
_US_2026 = _CONFIG_DIR / "US_2026.yaml"


def _venue_calendar_from_yaml(path: Path, venue: Venue) -> VenueCalendar:
    """`load_calendar`가 반환한 `CalendarDay` 목록을 `PostgresCalendarRepository.
    load`와 동일한 규칙(휴장/조기폐장만 예외 집합)으로 `VenueCalendar`에
    조립한다 — DB 없이 순수 도메인 계층을 실제 공시 데이터로 재생하기 위한
    테스트 전용 배선이다(프로덕션 로직 재구현이 아니라 테스트 입력 준비)."""
    days = load_calendar(path)
    session = KNOWN_SESSIONS[venue.value]
    holidays = {d.trade_date for d in days if not d.is_trading_day}
    early_closes = {
        d.trade_date: d.close_at.astimezone(session.tz).time()
        for d in days
        if d.early_close and d.close_at is not None
    }
    return VenueCalendar(
        venue=venue.value,
        tz=session.tz,
        regular=session,
        holidays=frozenset(holidays),
        early_closes=early_closes,
    )


# ---- 실패 주입 ----


def test_file_read_os_error_propagates_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """파일을 읽을 수 없는 상황(권한 없음·마운트 끊김 등)에서 `load_calendar`가
    예외를 삼키고 빈 캘린더를 반환하는 대신 그대로 전파해야 한다 — 조용한
    실패는 §4.1 fail-closed 위반이다."""

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("injected file read failure")

    monkeypatch.setattr(Path, "open", _boom)
    with pytest.raises(OSError):
        load_calendar(_KRX_2026)


def test_malformed_yaml_syntax_propagates_not_silently_empty(tmp_path: Path) -> None:
    """들여쓰기가 깨진 yaml(파싱 자체가 실패)이 빈 캘린더로 위장되지 않고
    `yaml.YAMLError`로 그대로 전파돼야 한다."""
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        "source: https://example.com/official-notice\n"
        "collected_at: 2026-09-07\n"
        "venue: KIS_KRX\n"
        "holidays:\n"
        "  - 2026-01-01\n"
        " bad_indent_key: [oops\n",  # 닫히지 않은 flow 시퀀스 + 잘못된 들여쓰기
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError):
        load_calendar(bad)


def test_early_close_missing_close_time_key_propagates(tmp_path: Path) -> None:
    """`early_closes` 항목에 `close_time`이 빠지면(공시 원문을 절반만 옮겨
    적은 상황을 흉내) `KeyError`가 조용히 무시되지 않고 그대로 전파돼야
    한다 — 조기폐장 시각을 정규 마감으로 조용히 대체하면 안 된다."""
    bad = tmp_path / "bad_early_close.yaml"
    bad.write_text(
        "source: https://example.com/official-notice\n"
        "collected_at: 2026-09-07\n"
        "venue: KIS_US\n"
        "holidays: []\n"
        "early_closes:\n"
        "  - date: 2026-11-27\n",
        encoding="utf-8",
    )
    with pytest.raises(KeyError):
        load_calendar(bad)


def test_invalid_close_time_format_propagates(tmp_path: Path) -> None:
    """공시 원문을 옮겨 적다 시각 포맷을 잘못 쓴 경우(`25:99`) `ValueError`가
    그대로 전파돼야 한다 — 파싱 실패를 기본값으로 눙치지 않는다."""
    bad = tmp_path / "bad_time.yaml"
    bad.write_text(
        "source: https://example.com/official-notice\n"
        "collected_at: 2026-09-07\n"
        "venue: KIS_US\n"
        "holidays: []\n"
        "early_closes:\n"
        "  - date: 2026-11-27\n"
        "    close_time: '25:99'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_calendar(bad)


# ---- 성능 단언 ----


@pytest.mark.perf
def test_load_calendar_large_holiday_list_meets_latency_budget(tmp_path: Path) -> None:
    """5,000건 휴장일(다자산·다년도 벤더 통합 시나리오를 흉내)을 담은 yaml
    파싱이 절대시간 예산 내여야 한다."""
    n = 5_000
    budget_sec = 15.0  # 실측 로컬 <0.5s, 동일 세션에서 다른 테스트 파일과 병행 시 디스크/백신
    # 경합으로 최대 ~9s까지 관측됨 — CI 환경 편차를 넉넉히 흡수한다
    big = tmp_path / "big.yaml"
    lines = [
        "source: https://example.com/official-notice",
        "collected_at: 2026-09-07",
        "venue: KIS_KRX",
        "holidays:",
    ]
    base = date(2000, 1, 3)  # 월요일부터 시작, 평일만 채워 넣는다
    d = base
    count = 0
    while count < n:
        if d.weekday() < 5:
            lines.append(f"  - {d.isoformat()}")
            count += 1
        d = date.fromordinal(d.toordinal() + 1)
    lines.append("early_closes: []")
    big.write_text("\n".join(lines) + "\n", encoding="utf-8")

    start = time.perf_counter()
    days = load_calendar(big)
    elapsed = time.perf_counter() - start

    print(
        f"[RD-21 yaml_calendar_source] load_calendar({n}) in {elapsed:.3f}s (budget<{budget_sec}s)"
    )
    assert len(days) == n
    assert elapsed < budget_sec, (
        f"대량 휴장일 파싱이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_venue_calendar_repeated_is_open_queries_meet_throughput_budget() -> None:
    """실제 KRX 공시 캘린더로 조립한 `VenueCalendar.is_open()` 반복 조회(주문
    라우팅 경로에서 매 주문마다 호출되는 것을 흉내)가 처리량 예산을 지켜야
    한다."""
    calendar = _venue_calendar_from_yaml(_KRX_2026, Venue.KIS_KRX)
    iterations = 50_000
    budget_sec = 15.0  # 실측 로컬 <1s, 동일 세션 병행 실행 시 최대 ~8s까지 관측됨 — CI 편차 흡수
    tz = calendar.tz

    start = time.perf_counter()
    for i in range(iterations):
        day = date(2026, 1, 1) + (date(2026, 12, 31) - date(2026, 1, 1)) * (i % 365) // 365
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=tz)
        calendar.is_open(at)
    elapsed = time.perf_counter() - start

    print(f"[RD-21 session_rules] is_open() x{iterations} in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"is_open 반복 조회가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 추수감사절 주간 타임라인 재생 ----


def test_thanksgiving_week_open_close_timeline_matches_official_notice() -> None:
    """실제 US_2026.yaml(NYSE 공식 공시)로 조립한 `VenueCalendar`를 추수감사절
    주간 5단계로 재생한다: 정규 개장(11/25) -> 휴장(11/26) -> 조기폐장(11/27,
    13:00 마감) -> 주말(11/28) -> 정규 개장 복귀(11/30). 각 전이에서 이전
    단계의 개폐 시각이 다음 단계로 새지 않음을 분단위 경계로 증명한다 —
    조기폐장 마감시각이 그 다음 정규 개장일 마감시각까지 밀려나오면(회귀)
    이 테스트가 결정적으로 적색이 된다."""
    calendar = _venue_calendar_from_yaml(_US_2026, Venue.KIS_US)
    tz = ZoneInfo("America/New_York")

    def _at(y: int, m: int, d: int, h: int, mi: int) -> datetime:
        return datetime(y, m, d, h, mi, tzinfo=tz)

    # 0단계: 11/25(수) 정규 개장일 — 09:30 개장, 16:00 정규 마감.
    assert calendar.is_open(_at(2026, 11, 25, 9, 29)) is False
    assert calendar.is_open(_at(2026, 11, 25, 9, 30)) is True
    assert calendar.is_open(_at(2026, 11, 25, 15, 59)) is True
    assert calendar.is_open(_at(2026, 11, 25, 16, 0)) is False

    # 1단계: 11/26(목) 추수감사절 — 공시 원문의 전일 개장 상태가 새지 않고
    # 종일 휴장이어야 한다.
    assert calendar.is_open(_at(2026, 11, 26, 9, 30)) is False
    assert calendar.is_open(_at(2026, 11, 26, 12, 0)) is False
    assert calendar.is_open(_at(2026, 11, 26, 15, 59)) is False

    # 2단계: 11/27(금) 조기폐장 — 개장은 정상, 마감만 13:00으로 앞당겨진다.
    assert calendar.is_open(_at(2026, 11, 27, 9, 30)) is True
    assert calendar.is_open(_at(2026, 11, 27, 12, 59)) is True
    assert calendar.is_open(_at(2026, 11, 27, 13, 0)) is False
    # 정규 마감시각(16:00)은 조기폐장일에는 이미 마감 이후다 — 정규 마감이
    # 조기폐장 위로 새어 들어와 개장 상태를 연장하면 안 된다.
    assert calendar.is_open(_at(2026, 11, 27, 15, 59)) is False

    # 3단계: 11/28(토) 주말 — 전일 조기폐장 예외가 주말 규칙을 덮어쓰지 않는다.
    assert calendar.is_open(_at(2026, 11, 28, 10, 0)) is False

    # 4단계: 11/30(월) 정규 개장 복귀 — 금요일의 13:00 조기폐장이 월요일까지
    # 새어 나와 마감시각을 앞당기면 안 된다(정규 16:00 마감 그대로).
    assert calendar.is_open(_at(2026, 11, 30, 13, 0)) is True
    assert calendar.is_open(_at(2026, 11, 30, 15, 59)) is True
    assert calendar.is_open(_at(2026, 11, 30, 16, 0)) is False
