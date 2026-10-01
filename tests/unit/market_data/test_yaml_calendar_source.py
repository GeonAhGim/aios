"""LA-12 — yaml_calendar_source.load_calendar 단위 테스트(task-1768).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-12, §10 R4.
ADR-2026-09-06-H D3 DoD: KRX·미국 당해·익년 휴장일이 출처 URL과 함께
등재되고 UNVERIFIED 0건, 임의 날짜 개장 여부가 공시 원문과 일치, 미수집
연도는 추측하지 않고 거부한다.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any

import pytest

from src.foundation.market_data.adapters.yaml_calendar_source import (
    CalendarSourceError,
    load_calendar,
)
from src.foundation.market_data.contracts.v1 import Venue

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "market_calendars"
_KRX_2026 = _CONFIG_DIR / "KRX_2026.yaml"
_KRX_2027 = _CONFIG_DIR / "KRX_2027.yaml"
_US_2026 = _CONFIG_DIR / "US_2026.yaml"
_US_2027 = _CONFIG_DIR / "US_2027.yaml"


@pytest.mark.parametrize("path", [_KRX_2026, _KRX_2027, _US_2026, _US_2027])
def test_no_row_carries_unverified_source(path: Path) -> None:
    days = load_calendar(path)
    assert days, f"{path.name}에 최소 1개 행이 있어야 한다"
    assert all(day.source != "UNVERIFIED" for day in days)
    assert all("http" in day.source and "collected_at=" in day.source for day in days)


def test_krx_2026_matches_official_notice_dates() -> None:
    days = {d.trade_date: d for d in load_calendar(_KRX_2026)}
    assert len(days) == 17
    assert all(d.venue == Venue.KIS_KRX for d in days.values())
    # 제헌절(부활) — 공시 원문에 있는 날짜, 지어낸 값이 아니다.
    assert date(2026, 7, 17) in days
    assert days[date(2026, 7, 17)].is_trading_day is False
    # 현충일(6/6, 토요일)은 공시에 없다 — 주말과 겹치는 날은 별도 등재하지 않는다.
    assert date(2026, 6, 6) not in days


def test_krx_2027_already_published_by_krx() -> None:
    days = {d.trade_date: d for d in load_calendar(_KRX_2027)}
    assert len(days) == 16
    assert date(2027, 3, 1) in days  # 삼일절(2027년은 월요일이라 대체공휴일 없음)
    assert date(2027, 3, 2) not in days


def test_us_2026_holiday_and_early_close_matches_nyse_notice() -> None:
    days = {d.trade_date: d for d in load_calendar(_US_2026)}
    assert all(d.venue == Venue.KIS_US for d in days.values())

    thanksgiving = days[date(2026, 11, 26)]
    assert thanksgiving.is_trading_day is False

    early_close = days[date(2026, 11, 27)]
    assert early_close.is_trading_day is True
    assert early_close.early_close is True
    assert early_close.close_at is not None
    assert early_close.close_at.time() == time(13, 0)
    assert early_close.close_at.utcoffset() is not None


def test_us_2027_independence_day_observed_matches_nyse_notice() -> None:
    days = {d.trade_date: d for d in load_calendar(_US_2027)}
    # 2027-07-04는 일요일이라 대체휴장일이 07-05(월)로 옮겨진다.
    assert date(2027, 7, 5) in days
    assert date(2027, 7, 4) not in days


def test_missing_collected_at_field_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: https://example.com/official-notice\n"
        "venue: KIS_KRX\n"
        "holidays: [2026-01-01]\n"
        "early_closes: []\n",
        encoding="utf-8",
    )
    with pytest.raises(CalendarSourceError):
        load_calendar(bad)


def test_missing_source_field_still_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "collected_at: 2026-09-07\nvenue: KIS_KRX\nholidays: [2026-01-01]\n",
        encoding="utf-8",
    )
    with pytest.raises(CalendarSourceError):
        load_calendar(bad)


def test_forged_holiday_not_matching_primary_source_would_fail_assertion() -> None:
    """DoD 반증 가능성 확인: 공시 원문에 없는 날짜를 휴장일로 잘못 주장하면
    이 테스트 스타일의 assertion이 실제로 깨진다는 것을 스스로 증명한다."""
    days = {d.trade_date: d for d in load_calendar(_KRX_2026)}
    fabricated_holiday = date(2026, 4, 1)  # KRX가 공시하지 않은 날짜
    assert fabricated_holiday not in days


def test_invalid_date_format_in_holidays_raises(tmp_path: Path) -> None:
    """YAML holidays 목록에 잘못된 날짜 형식(월/일이 범위를 벗어난 값)을
     입력했을 때 로더가 예외를 던짐을 단언 — 날짜 파싱 실패는 ValueError
    로 전파된다(현재 구현). 로더가 조용히 통과시키지 않음을 확인."""
    bad = tmp_path / "bad_date.yaml"
    bad.write_text(
        "source: https://example.com/notice\n"
        "collected_at: 2026-09-07\n"
        "venue: KIS_KRX\n"
        "holidays: ['2026-13-40']\n"  # 월 13, 일 40 — 모두 범위를 벗어남
        "early_closes: []\n",
        encoding="utf-8",
    )
    # 현재 구현: _parse_date가 date.fromisoformat 실패 시 ValueError 던짐
    with pytest.raises(ValueError):
        load_calendar(bad)


def test_missing_venue_field_raises(tmp_path: Path) -> None:
    """스키마상 필수 키 'venue'가 누락되면 로더가 CalendarSourceError를 던짐."""
    bad = tmp_path / "bad_venue.yaml"
    bad.write_text(
        "source: https://example.com/notice\n"
        "collected_at: 2026-09-07\n"
        "holidays: [2026-01-01]\n"
        "early_closes: []\n",
        encoding="utf-8",
    )
    with pytest.raises(CalendarSourceError):
        load_calendar(bad)


def test_file_read_failure_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """실패주입: yaml.safe_load 의존성이 예외를 던지도록 monkeypatch하면
    호출부(load_calendar)가 이를 삼키지 않고 전파(또는 래핑)됨을 확인."""
    import yaml as _yaml_module

    def _fake_safe_load(*args: Any, **kwargs: Any) -> None:
        raise OSError("device or resource busy")

    monkeypatch.setattr(_yaml_module, "safe_load", _fake_safe_load)

    good = tmp_path / "good.yaml"
    good.write_text(
        "source: https://example.com/notice\n"
        "collected_at: 2026-09-07\n"
        "venue: KIS_KRX\n"
        "holidays: [2026-01-01]\n"
        "early_closes: []\n",
        encoding="utf-8",
    )
    # safe_load가 OSError를 던지면 load_calendar는 이를 caught하지 않고 그대로 전파해야 함.
    with pytest.raises(OSError):
        load_calendar(good)


def test_load_calendar_performance_under_10ms() -> None:
    """성능 단언: 전체 config 파일(최대 4개)을 로드하는 데 10ms 미만이어야 한다.
    캘린더 로더는 I/O 바운드가 아니므로 마이크로초 단위가 목표."""
    import time

    start = time.perf_counter_ns()
    for p in [_KRX_2026, _KRX_2027, _US_2026, _US_2027]:
        load_calendar(p)
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    assert elapsed_ms < 10, f"4개 파일 로드 총소요 {elapsed_ms:.1f}ms — 예산 10ms 초과"
