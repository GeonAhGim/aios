"""LA-6 — market_data/domain/quality/verdict.py 배치 판정 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §4.1, §8.1, §9.2 LA-6.

핵심 케이스(§8.1): REJECT 비율 20% 경계 — 20.0%는 QUARANTINE이 아니고
20.1%는 QUARANTINE. 그 외 ACCEPT(이슈 없음)·total<=0 fail-closed도 검증.
"""
from datetime import datetime, timezone

from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    Severity,
    Verdict,
)
from src.foundation.market_data.domain.quality.verdict import decide

UTC = timezone.utc


def _reject_issue(n: int) -> QualityIssue:
    return QualityIssue(
        type=QualityIssueType.OHLC_INCONSISTENT,
        severity=Severity.REJECT,
        open_time=datetime(2026, 9, 1, tzinfo=UTC),
        detail={"n": str(n)},
    )


def test_decide_accepts_when_no_issues() -> None:
    result = decide([], total=10)

    assert result.verdict is Verdict.ACCEPT
    assert result.accepted == 10
    assert result.quarantined == 0
    assert result.rejected == 0


def test_decide_reject_ratio_exactly_20_percent_is_not_quarantine() -> None:
    issues = [_reject_issue(i) for i in range(200)]

    result = decide(issues, total=1000)

    assert result.verdict is Verdict.PARTIAL
    assert result.accepted == 800
    assert result.rejected == 200
    assert result.quarantined == 0


def test_decide_reject_ratio_over_20_percent_is_quarantine() -> None:
    issues = [_reject_issue(i) for i in range(201)]

    result = decide(issues, total=1000)

    assert result.verdict is Verdict.QUARANTINE
    assert result.accepted == 0
    assert result.quarantined == 1000
    assert result.rejected == 0


def test_decide_zero_total_fails_closed_to_reject() -> None:
    result = decide([], total=0)

    assert result.verdict is Verdict.REJECT
    assert result.accepted == 0
    assert result.quarantined == 0
    assert result.rejected == 0


def test_decide_negative_total_fails_closed_to_reject() -> None:
    result = decide([], total=-1)

    assert result.verdict is Verdict.REJECT
