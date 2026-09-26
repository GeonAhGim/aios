"""Research-data coverage alerts.

Detects two failure modes for research-data sources:
  * freshness age exceeds a configurable threshold,
  * consecutive collection failures reach a configured limit.

Uses ``src/core/safety/data_freshness.py``
(``DataFreshnessTracker``) as the freshness-recording infrastructure
rather than re-implementing the recording logic.

Public API
----------
* ``check_freshness_alerts``  -- scan a set of sources and return alerts for
  freshness violations.
* ``record_collection_failure`` / ``check_source_failures`` -- track per-source
  consecutive-failure counts and return alerts when the limit is reached.
* ``check_all_coverage_alerts`` -- convenience entry point (freshness + failures).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from src.core.safety.data_freshness import DataFreshnessTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# "freshness" | "consecutive_failure"


@dataclass(frozen=True)
class CoverageAlert:
    """One alert emitted by the coverage-alert system."""

    source_id: str
    kind: str  # "freshness" | "consecutive_failure"
    severity: str  # "warning" | "critical"
    message: str
    age_seconds: Decimal | None = None
    consecutive_failures: int | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Freshness alerts  (wraps DataFreshnessTracker)
# ---------------------------------------------------------------------------


def check_freshness_alerts(
    tracker: DataFreshnessTracker,
    *,
    now: datetime | None = None,
    freshness_threshold: Decimal = Decimal("3600"),
) -> list[CoverageAlert]:
    """Return alerts for any source whose freshness age exceeds *threshold*.

    Parameters
    ----------
    tracker:
        A ``DataFreshnessTracker`` instance that has been recording
        ``record(exchange, symbol, close_time)`` calls.
    now:
        Reference time for testing; defaults to ``datetime.now(timezone.utc)``.
    freshness_threshold:
        Maximum acceptable age in seconds (Decimal).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    alerts: list[CoverageAlert] = []
    max_delay = tracker.max_delay_sec(now)

    # max_delay_sec returns None when no observations exist at all —
    # treat "no data" as a critical freshness issue.
    if max_delay is None:
        alerts.append(
            CoverageAlert(
                source_id="all",
                kind="freshness",
                severity="critical",
                message="No freshness observations recorded — possible source outage",
            )
        )
    elif max_delay > freshness_threshold:
        severity = "critical" if max_delay > freshness_threshold * 2 else "warning"
        alerts.append(
            CoverageAlert(
                source_id="all",
                kind="freshness",
                severity=severity,
                message=(
                    f"Research-data freshness age {max_delay:.0f}s exceeds "
                    f"threshold {freshness_threshold:.0f}s"
                ),
                age_seconds=max_delay,
            )
        )
    return alerts


# ---------------------------------------------------------------------------
# Consecutive-failure tracking
# ---------------------------------------------------------------------------

# In-memory failure registry keyed by source_id.
# Each entry holds the current consecutive-failure count.
_failure_registry: dict[str, int] = {}


def record_collection_failure(source_id: str) -> None:
    """Increment the consecutive-failure counter for *source_id*.

    Call this each time a collection attempt fails for the given source.
    Use ``reset_failure_count`` on success.
    """
    _failure_registry[source_id] = _failure_registry.get(source_id, 0) + 1


def reset_failure_count(source_id: str) -> None:
    """Reset the consecutive-failure counter for *source_id* to zero."""
    _failure_registry[source_id] = 0


def get_consecutive_failures(source_id: str) -> int:
    """Return the current consecutive-failure count for *source_id*."""
    return _failure_registry.get(source_id, 0)


def check_source_failures(
    sources: Sequence[str],
    *,
    failure_threshold: int = 3,
) -> list[CoverageAlert]:
    """Return alerts for any source whose consecutive failures >= *threshold*."""
    alerts: list[CoverageAlert] = []
    for source_id in sources:
        count = get_consecutive_failures(source_id)
        if count >= failure_threshold:
            alerts.append(
                CoverageAlert(
                    source_id=source_id,
                    kind="consecutive_failure",
                    severity="critical",
                    message=(
                        f"Source {source_id} has {count} consecutive collection failures "
                        f"(threshold={failure_threshold})"
                    ),
                    consecutive_failures=count,
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


def check_all_coverage_alerts(
    tracker: DataFreshnessTracker,
    sources: Sequence[str],
    *,
    now: datetime | None = None,
    freshness_threshold: Decimal = Decimal("3600"),
    failure_threshold: int = 3,
) -> list[CoverageAlert]:
    """Run both freshness and consecutive-failure checks.

    Returns alerts sorted by severity (critical first) then source_id.
    """
    freshness_alerts = check_freshness_alerts(
        tracker, now=now, freshness_threshold=freshness_threshold
    )
    failure_alerts = check_source_failures(sources, failure_threshold=failure_threshold)

    all_alerts = freshness_alerts + failure_alerts
    # Sort: critical before warning, then by source_id
    all_alerts.sort(key=lambda a: (0 if a.severity == "critical" else 1, a.source_id))
    return all_alerts
