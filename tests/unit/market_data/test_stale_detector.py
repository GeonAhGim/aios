"""LA-5 — market_data/domain/quality/stale_detector.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §8.1, §9.2 LA-5.

핵심 케이스(§8.1): 세션 밖 스테일 아님, 세션 내 `3×duration+1s` STALE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from src.foundation.market_data.contracts.v1 import QualityIssueType, Severity, Timeframe
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError, duration

UTC = timezone.utc
_LAST = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def test_session_closed_never_stale_regardless_of_elapsed() -> None:
    now = _LAST + timedelta(days=1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=False) is None


def test_session_open_exceeds_3x_duration_plus_1s_is_stale() -> None:
    now = _LAST + 3 * duration(Timeframe.M1) + timedelta(seconds=1)
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True)
    assert issue is not None
    assert issue.type is QualityIssueType.STALE
    assert issue.severity is Severity.WARN
    assert issue.open_time == _LAST


def test_session_open_exactly_at_threshold_is_not_stale() -> None:
    now = _LAST + 3 * duration(Timeframe.M1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=True) is None


def test_custom_k_changes_threshold() -> None:
    now = _LAST + 2 * duration(Timeframe.M1) + timedelta(seconds=1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=True, k=3) is None
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True, k=2)
    assert issue is not None
    assert issue.type is QualityIssueType.STALE


def test_naive_last_ts_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        detect_stale(datetime(2026, 9, 3, 10, 0), _LAST, Timeframe.M1, session_open=True)


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        detect_stale(_LAST, datetime(2026, 9, 3, 10, 5), Timeframe.M1, session_open=True)


def test_unknown_timeframe_raises() -> None:
    with pytest.raises(UnknownTimeframeError):
        detect_stale(_LAST, _LAST, cast(Timeframe, "2h"), session_open=True)
