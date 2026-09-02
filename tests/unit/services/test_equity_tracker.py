"""ExecutionEquityTracker의 seed/영속화 연동 지점 단위테스트 — DB 없이
순수 메모리 로직만 검증(PM 배정 ③, 2026-09-02). DB 왕복 자체
(load_equity_baseline/save_equity_baseline/record_and_persist_equity)는
strategy_executions에 컬럼 마이그레이션이 적용된 뒤 통합테스트로 별도
검증한다.
"""
from datetime import date
from decimal import Decimal

from src.services.execution_loop.equity_tracker import ExecutionEquityTracker


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
