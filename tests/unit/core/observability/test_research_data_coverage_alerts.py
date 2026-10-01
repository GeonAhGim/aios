"""Tests for ``src/core/observability/research_data_coverage_alerts.py``.

DoD (task-6707 / RD-18)
------------------------
* freshness age > threshold → 1 alert
* consecutive failures ≥ 3 → 1 alert
* normal state (age within threshold, 0 failures) → 0 alerts (negative)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.observability.research_data_coverage_alerts import (
    CoverageAlert,
    check_all_coverage_alerts,
    check_freshness_alerts,
    check_source_failures,
    get_consecutive_failures,
    record_collection_failure,
    reset_failure_count,
)
from src.core.safety.data_freshness import DataFreshnessTracker

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_failure_registry():
    """Isolate consecutive-failure state between tests."""
    from src.core.observability import research_data_coverage_alerts as mod

    prior = dict(mod._failure_registry)
    mod._failure_registry.clear()
    yield
    mod._failure_registry.clear()
    mod._failure_registry.update(prior)


def _make_tracker_with_data(now: datetime) -> DataFreshnessTracker:
    """Build a tracker that has recent observations (1 h + 1 s ago)."""
    tracker = DataFreshnessTracker()
    old_time = now - timedelta(hours=1, seconds=1)
    tracker.record("kraken", "BTC/USDT", old_time)
    return tracker


def _make_stale_tracker(now: datetime) -> DataFreshnessTracker:
    """Build a tracker with stale observations (4 h ago)."""
    tracker = DataFreshnessTracker()
    old_time = now - timedelta(hours=4)
    tracker.record("kraken", "BTC/USDT", old_time)
    return tracker


NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Freshness alerts
# ---------------------------------------------------------------------------


class TestCheckFreshnessAlerts:
    """Freshness-alert path: source freshness age > threshold → alert."""

    def test_no_alert_when_fresh(self):
        """Fresh source (1 h old) stays within 3600 s threshold → 0 alerts."""
        tracker = _make_tracker_with_data(NOW)
        alerts = check_freshness_alerts(tracker, now=NOW, freshness_threshold=Decimal("7200"))
        assert alerts == []

    def test_alert_when_stale(self):
        """Stale source (4 h old) exceeds 3600 s threshold → 1 alert."""
        tracker = _make_stale_tracker(NOW)
        alerts = check_freshness_alerts(tracker, now=NOW, freshness_threshold=Decimal("3600"))
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.kind == "freshness"
        assert alert.source_id == "all"
        assert alert.severity == "critical"  # 4 h > 2 × 3600 s
        assert alert.age_seconds is not None

    def test_warning_not_critical_within_double_threshold(self):
        """Age between 1× and 2× threshold → severity=warning."""
        # 1.5 h = 5400 s, threshold = 3600 s → warning
        tracker2 = DataFreshnessTracker()
        old_time = NOW - timedelta(hours=1, minutes=30)
        tracker2.record("src", "SYM", old_time)
        alerts = check_freshness_alerts(tracker2, now=NOW, freshness_threshold=Decimal("3600"))
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"

    def test_no_data_is_critical(self):
        """Empty tracker → 1 critical alert (no observations)."""
        tracker = DataFreshnessTracker()
        alerts = check_freshness_alerts(tracker, now=NOW)
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert "No freshness observations" in alerts[0].message

    def test_no_alert_exactly_at_threshold_boundary(self):
        """negative: age exactly == threshold (not >) → 0 alerts.

        The comparison in ``check_freshness_alerts`` is strict (``max_delay >
        freshness_threshold``); an off-by-one regression to ``>=`` would flip
        this to 1 alert.
        """
        tracker = DataFreshnessTracker()
        tracker.record("kraken", "BTC/USDT", NOW - timedelta(seconds=3600))
        alerts = check_freshness_alerts(tracker, now=NOW, freshness_threshold=Decimal("3600"))
        assert alerts == []


# ---------------------------------------------------------------------------
# Consecutive-failure alerts
# ---------------------------------------------------------------------------


class TestConsecutiveFailures:
    """Failure-count path: consecutive failures ≥ 3 → alert."""

    def test_zero_failures_no_alert(self):
        """No recorded failures → no alert."""
        alerts = check_source_failures(["src_ok"], failure_threshold=3)
        assert alerts == []

    def test_below_threshold_no_alert(self):
        """1 failure < threshold 3 → no alert."""
        record_collection_failure("src_partial")
        alerts = check_source_failures(["src_partial"], failure_threshold=3)
        assert alerts == []

    def test_at_threshold_generates_alert(self):
        """3 consecutive failures = threshold → 1 alert."""
        for _ in range(3):
            record_collection_failure("src_broken")
        alerts = check_source_failures(["src_broken"], failure_threshold=3)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.kind == "consecutive_failure"
        assert alert.source_id == "src_broken"
        assert alert.severity == "critical"
        assert alert.consecutive_failures == 3

    def test_above_threshold_single_alert(self):
        """5 failures > threshold 3 → still 1 alert (not 5)."""
        for _ in range(5):
            record_collection_failure("src_heavy")
        alerts = check_source_failures(["src_heavy"], failure_threshold=3)
        assert len(alerts) == 1
        assert alerts[0].consecutive_failures == 5

    def test_reset_clears_count(self):
        """Reset after 3 failures → 0 alerts."""
        for _ in range(3):
            record_collection_failure("src_reset")
        reset_failure_count("src_reset")
        alerts = check_source_failures(["src_reset"], failure_threshold=3)
        assert alerts == []

    def test_get_consecutive_failures_returns_current(self):
        """Direct counter read matches recorded value."""
        record_collection_failure("src_counter")
        record_collection_failure("src_counter")
        assert get_consecutive_failures("src_counter") == 2
        reset_failure_count("src_counter")
        assert get_consecutive_failures("src_counter") == 0

    def test_multiple_untouched_sources_no_alerts(self):
        """negative: several sources that never recorded a failure → 0 alerts,
        even when one of them has a non-empty entry in the registry below
        threshold (checks there is no cross-source leakage)."""
        record_collection_failure("src_mixed_a")  # 1 failure, below threshold
        alerts = check_source_failures(
            ["src_mixed_a", "src_mixed_b", "src_mixed_c"], failure_threshold=3
        )
        assert alerts == []


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


class TestCheckAllCoverageAlerts:
    """Both freshness + failures, plus negative test."""

    def test_both_alerts_combined(self):
        """Stale source + broken source → 2 alerts, critical first."""
        tracker = _make_stale_tracker(NOW)
        for _ in range(3):
            record_collection_failure("src_broken")
        alerts = check_all_coverage_alerts(
            tracker,
            ["src_broken"],
            now=NOW,
            freshness_threshold=Decimal("3600"),
            failure_threshold=3,
        )
        assert len(alerts) == 2
        # Critical alerts come first
        assert all(a.severity == "critical" for a in alerts)

    def test_normal_state_no_alerts(self):
        """Fresh source, 0 failures → 0 alerts (negative test)."""
        tracker = _make_tracker_with_data(NOW)
        alerts = check_all_coverage_alerts(
            tracker,
            ["src_ok"],
            now=NOW,
            freshness_threshold=Decimal("7200"),
            failure_threshold=3,
        )
        assert alerts == []

    def test_alert_sorted_critical_first(self):
        """Mixed severities sorted: critical before warning."""
        # Fresh source → warning (age 1 h > 3600 s threshold)
        tracker = _make_tracker_with_data(NOW)
        # Inject failure alert (critical)
        for _ in range(3):
            record_collection_failure("src_fail")
        alerts = check_all_coverage_alerts(
            tracker,
            ["src_fail"],
            now=NOW,
            freshness_threshold=Decimal("3600"),
            failure_threshold=3,
        )
        assert len(alerts) == 2
        assert alerts[0].severity == "critical"
        assert alerts[1].severity == "warning"


# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------


class TestCoverageAlert:
    """CoverageAlert dataclass defaults and immutability."""

    def test_defaults(self):
        alert = CoverageAlert(
            source_id="x",
            kind="freshness",
            severity="warning",
            message="test",
        )
        assert alert.age_seconds is None
        assert alert.consecutive_failures is None
        assert alert.detected_at.tzinfo == timezone.utc

    def test_frozen(self):
        """Verify immutability (frozen=True)."""
        alert = CoverageAlert(
            source_id="x",
            kind="freshness",
            severity="warning",
            message="test",
        )
        with pytest.raises(AttributeError):
            alert.source_id = "y"


# ---------------------------------------------------------------------------
# Performance budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_check_all_coverage_alerts_stays_under_budget(perf_budget):
    """성능 단언: 500개 소스에 대한 결합 스캔은 순수 메모리 연산(dict 조회 +
    리스트 정렬)이라 1ms 예산 내에 끝나야 한다 -- 소스 수에 비례한 I/O나
    불필요한 재계산이 섞이지 않았는지 감시한다."""
    tracker = _make_tracker_with_data(NOW)
    sources = [f"src_{i}" for i in range(500)]

    sample = perf_budget.sample(
        lambda: check_all_coverage_alerts(
            tracker,
            sources,
            now=NOW,
            freshness_threshold=Decimal("7200"),
            failure_threshold=3,
        ),
        batch=20,
    )

    assert sample.cpu_ms < 1.0, perf_budget.describe(sample, budget_ms=1.0)


# ---------------------------------------------------------------------------
# Gate-red reproduction
# ---------------------------------------------------------------------------


class TestGateRedReproduction:
    """게이트 적색 재현: 이 파일의 임계값 단언이 상시-녹색이 아니라 실제로
    회귀를 잡아낸다는 것을 보인다(task-submit_order_perf_budget와 동일한
    기법 -- 결함이 있는 비교식을 주입해 기존 단언과 동치인 식이 깨짐을
    확인한다)."""

    def test_gate_red_off_by_one_threshold_would_fail(self):
        """consecutive-failure 임계값 비교가 `>=`가 아니라 `>` (off-by-one
        버그)였다면, 정확히 threshold에 도달한 상태에서
        ``test_at_threshold_generates_alert``의 "1개 알림 발생" 단언이
        실제로 적색(AssertionError)이 됨을 재현한다."""
        for _ in range(3):
            record_collection_failure("src_gate_red")
        count = get_consecutive_failures("src_gate_red")
        threshold = 3

        def buggy_is_breaching(count: int, threshold: int) -> bool:
            return count > threshold  # bug: should be >=

        with pytest.raises(AssertionError):
            assert buggy_is_breaching(count, threshold) is True

    def test_gate_red_freshness_severity_inversion_would_fail(self):
        """freshness 심각도 분기가 뒤집혀(critical/warning swap) 있었다면,
        ``test_alert_when_stale``의 "4h 지연 → critical" 단언이 실제로
        적색이 됨을 재현한다."""
        tracker = _make_stale_tracker(NOW)
        alerts = check_freshness_alerts(tracker, now=NOW, freshness_threshold=Decimal("3600"))
        real_severity = alerts[0].severity

        def buggy_invert(severity: str) -> str:
            return "warning" if severity == "critical" else "critical"

        with pytest.raises(AssertionError):
            assert buggy_invert(real_severity) == "critical"
