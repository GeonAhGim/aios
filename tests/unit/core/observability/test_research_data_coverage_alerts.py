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
