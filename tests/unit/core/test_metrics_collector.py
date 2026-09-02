"""ApiCallTracker 순수 로직 단위테스트 — DB 없이 검증(PM 배정 ⑤)."""
from decimal import Decimal

from src.core.safety.metrics_collector import ApiCallTracker


def test_error_rate_pct_zero_when_no_calls_recorded() -> None:
    tracker = ApiCallTracker()
    assert tracker.error_rate_pct() == Decimal("0")


def test_error_rate_pct_computes_failure_ratio() -> None:
    tracker = ApiCallTracker()
    for _ in range(3):
        tracker.record_success()
    for _ in range(1):
        tracker.record_failure()

    assert tracker.error_rate_pct() == Decimal("25")


def test_window_size_caps_history() -> None:
    """최근 N회만 반영 — 오래된 실패는 윈도우 밖으로 밀려나면 더 이상
    error_rate에 영향을 주지 않는다."""
    tracker = ApiCallTracker(window_size=3)
    tracker.record_failure()
    tracker.record_failure()
    tracker.record_failure()
    assert tracker.error_rate_pct() == Decimal("100")

    tracker.record_success()
    tracker.record_success()
    tracker.record_success()
    assert tracker.error_rate_pct() == Decimal("0")


def test_seconds_since_last_success_zero_before_any_success() -> None:
    tracker = ApiCallTracker()
    tracker.record_failure()
    assert tracker.seconds_since_last_success() == Decimal("0")


def test_seconds_since_last_success_tracks_elapsed_time() -> None:
    fake_now = [1000.0]
    tracker = ApiCallTracker(clock=lambda: fake_now[0])

    tracker.record_success()
    fake_now[0] += 12.5

    assert tracker.seconds_since_last_success() == Decimal("12.5")


def test_record_failure_after_success_does_not_reset_last_success_time() -> None:
    """실패는 "마지막 성공 시각"을 건드리지 않는다 — api_disconnect_sec가
    실패 하나로 리셋되면 진짜 장애 지속시간을 놓친다."""
    fake_now = [1000.0]
    tracker = ApiCallTracker(clock=lambda: fake_now[0])

    tracker.record_success()
    fake_now[0] += 5.0
    tracker.record_failure()
    fake_now[0] += 3.0

    assert tracker.seconds_since_last_success() == Decimal("8.0")
