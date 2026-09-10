"""ExecutionEquityTracker의 seed/영속화 연동 지점 단위테스트 — DB 없이
순수 메모리 로직만 검증(PM 배정 ③, 2026-09-02).

`save_equity_baseline`(실 DB 대상)은 tests/integration/services/test_equity_tracker.py로
분리했다(task-1615, PLT-36 — tests/unit 아래는 실DB에 접속하지 않는다).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from src.services.execution_loop.equity_tracker import ExecutionEquityTracker, _utc_today


def test_is_seeded_false_before_any_record_or_seed() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    assert tracker.is_seeded(1) is False


def test_seed_populates_baseline_when_memory_empty() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(
        1,
        day_start_date=date(2026, 9, 1),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1200"),
    )
    assert tracker.is_seeded(1) is True
    assert tracker.day_start(1) == (date(2026, 9, 1), Decimal("1000"))
    assert tracker.peak(1) == Decimal("1200")


def test_seed_with_none_values_does_not_mark_as_seeded() -> None:
    """DB에 아직 기준점이 없는(최초 실행) execution — seed가 아무것도
    못 채우면 is_seeded도 계속 False라 record()가 정상적으로 오늘을
    시작일로 잡는다."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(1, day_start_date=None, day_start_equity=None, peak_equity=None)
    assert tracker.is_seeded(1) is False


def test_seed_does_not_overwrite_already_recorded_value() -> None:
    """이 프로세스가 이미 한 번 record()한 execution에 뒤늦게 seed()가
    불려도(방어적 호출) 메모리 값을 덮어쓰지 않는다 — DB는 초기값
    용도일 뿐, record() 이후로는 메모리가 진실의 원천."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.record(1, Decimal("500"))

    tracker.seed(
        1,
        day_start_date=date(2026, 8, 1),
        day_start_equity=Decimal("999"),
        peak_equity=Decimal("999"),
    )

    assert tracker.day_start(1) == (date(2026, 9, 2), Decimal("500"))
    assert tracker.peak(1) == Decimal("500")


def test_seeded_baseline_feeds_into_record_daily_pnl() -> None:
    """재시작 복구 시나리오 — 오늘 이미 -2% 손실 중이었다면, seed 이후의
    첫 record()가 그 손실을 반영해야 한다(재시작으로 "오늘 시작"이
    리셋되면 안 됨)."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(
        1,
        day_start_date=date(2026, 9, 2),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )

    daily_pnl_pct, drawdown_pct = tracker.record(1, Decimal("980"))

    assert daily_pnl_pct == Decimal("-2")
    assert drawdown_pct == Decimal("2")


def test_default_clock_is_utc_fixed_not_local() -> None:
    """`date.today()`(OS 로컬 tz) 대신 UTC 고정 기본 clock을 쓴다."""
    tracker = ExecutionEquityTracker()
    assert tracker._today is _utc_today
    assert tracker._today() == datetime.now(timezone.utc).date()


def test_day_start_before_any_record_or_seed_raises_key_error() -> None:
    """호출부(`record_and_persist_equity`)는 반드시 `record()` 직후에만
    `day_start()`를 부른다는 계약이다 — 순서를 어기면 잘못된(0값 등)
    기본값을 조용히 반환하는 대신 즉시 KeyError로 fail-closed 해야 한다."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    try:
        tracker.day_start(1)
    except KeyError:
        pass
    else:
        raise AssertionError("day_start() must raise before record()/seed()")


def test_peak_before_any_record_or_seed_raises_key_error() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    try:
        tracker.peak(1)
    except KeyError:
        pass
    else:
        raise AssertionError("peak() must raise before record()/seed()")


def test_utc_today_diverges_from_os_local_timezone_at_day_boundary() -> None:
    """R-30이 요구하는 UTC 고정 일경계 실증 — UTC 자정 직후(00:30 UTC)
    시각을 고정하면, UTC-8 로컬 벽시계는 아직 전날(16:30, 전날 날짜)이다.
    `date.today()`(OS 로컬 tz)를 썼다면 day boundary가 하루 늦게
    잡혔을 것이라는 걸 같은 순간의 로컬 환산값과 대조해 증명한다."""
    fixed_instant = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)
    local_equivalent = fixed_instant.astimezone(timezone(timedelta(hours=-8)))
    assert local_equivalent.date() == date(2026, 9, 9)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_instant if tz is not None else fixed_instant.replace(tzinfo=None)

    with patch(
        "src.services.execution_loop.equity_tracker.datetime", _FixedDatetime
    ):
        result = _utc_today()

    assert result == date(2026, 9, 10)
    assert result != local_equivalent.date()
