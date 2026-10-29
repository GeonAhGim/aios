"""LA-6 — market_data/domain/quality/verdict.py 배치 판정 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §4.1, §8.1, §9.2 LA-6.

핵심 케이스(§8.1): REJECT 비율 20% 경계 — 20.0%는 QUARANTINE이 아니고
20.1%는 QUARANTINE. 그 외 ACCEPT(이슈 없음)·total<=0 fail-closed도 검증.

DEEPEN(task-2950, docs/audit/DEPTH_LA_LB_LC.md original task-390): this module
is a pure function with no I/O (same premise as `test_session_rules.py`'s
DEEPEN for LA-3), so the missing axes are translated rather than applied
literally: negative(3rd, `issues` inconsistent with `total`), failure
injection (corrupted `severity` that bypassed enum validation, via
`model_construct` — like `test_adjustment.py`'s corrupted `action_type`),
gate-red reproduction (pins the fix below against regression), numeric
performance, replay, and concurrency.

Failure injection found a real defect, fixed in the same commit:
`decide()` compared `issue.severity is Severity.REJECT` (identity) instead of
`==`. A `QualityIssue` built through normal pydantic validation always holds
the `Severity.REJECT` enum singleton, so `is` happened to work — but any
issue that reaches `decide()` after bypassing validation (e.g. deserialized
from a looser/older schema) could carry the plain string `"REJECT"`, which
`is` silently fails to recognize as REJECT-severity, under-counting
`rejected` and yielding a falsely lenient verdict (fail-open, violates I-07).
"""

import threading
from datetime import UTC, datetime

import pytest

from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    Severity,
    Verdict,
)
from src.foundation.market_data.domain.quality import verdict as verdict_mod
from src.foundation.market_data.domain.quality.verdict import decide
from tests.conftest import PerfBudget
from tests.support.perf_inject import delayed_sync

UTC = UTC


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


def _corrupted_reject_issue() -> QualityIssue:
    """Bypasses pydantic validation via `model_construct` to hold the plain
    string `"REJECT"` instead of the `Severity.REJECT` enum singleton --
    simulates an issue that reached `decide()` after deserializing from a
    looser/older schema (same technique as `test_adjustment.py`'s corrupted
    `action_type`)."""
    return QualityIssue.model_construct(
        type=QualityIssueType.OHLC_INCONSISTENT,
        severity="REJECT",
        open_time=datetime(2026, 9, 1, tzinfo=UTC),
        detail={},
        schema_version="v1",
    )


def test_decide_more_reject_issues_than_total_still_fails_closed_without_overflow() -> None:
    """Negative (3rd) -- beyond `total`'s own sign (already covered by the
    zero/negative-total tests above), a caller/reporting bug can hand
    `decide()` more REJECT-severity issues than there are candles in the
    batch. The ratio check must still route this to QUARANTINE with sane,
    non-negative counts instead of crashing or producing an inconsistent
    `accepted`/`quarantined` split."""
    issues = [_reject_issue(i) for i in range(10)]

    result = decide(issues, total=3)

    assert result.verdict is Verdict.QUARANTINE
    assert result.accepted == 0
    assert result.quarantined == 3
    assert result.rejected == 0


def test_decide_counts_severity_bypassing_enum_validation_as_rejected() -> None:
    """Failure injection -- pins the fix in this commit: a `QualityIssue`
    whose `severity` bypassed pydantic validation and holds the plain string
    `"REJECT"` (not the enum singleton) must still be counted toward
    `rejected` by `decide()`. Before the fix, `is`-identity comparison
    silently missed it, under-counting `rejected` (fail-open)."""
    issues = [_corrupted_reject_issue()]

    result = decide(issues, total=10)

    assert result.rejected == 1
    assert result.accepted == 9
    assert result.verdict is Verdict.PARTIAL


def test_gate_severity_identity_comparison_regression_undercounts_rejected() -> None:
    """Gate-red reproduction -- reconstructs the pre-fix `is`-identity
    comparison this commit replaced with `==` and shows it would silently
    under-count a corrupted-but-content-valid REJECT issue (0 instead of 1),
    proving the `==` fix in `decide()` is load-bearing and pinning it against
    regression."""
    corrupted = _corrupted_reject_issue()

    pre_fix_rejected = sum(1 for issue in [corrupted] if issue.severity is Severity.REJECT)
    post_fix_rejected = sum(1 for issue in [corrupted] if issue.severity == Severity.REJECT)

    assert pre_fix_rejected == 0
    assert post_fix_rejected == 1


@pytest.mark.perf
def test_decide_throughput_within_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """Numeric performance assertion -- 100,000 REJECT issues (pure in-memory
    arithmetic, no I/O) must be decided well inside a generous budget. A
    regression that turned the ratio check into something worse than O(n)
    would blow this budget long before it hurt in production.

    raw perf_counter() -> perf_budget.assert_within() 전환(task-11834):
    process_time 기반 CPU 시간 측정으로 다른 프로세스의 코어 경쟁 영향을 뺀다."""
    issues = [_reject_issue(i) for i in range(100_000)]
    last_result: Verdict | None = None

    def _run() -> None:
        nonlocal last_result
        last_result = decide(issues, total=1_000_000).verdict

    perf_budget.assert_within(
        _run,
        budget_ms=2000,
        n=5,
        warmup=1,
        batch=10,
        label="decide 100k issues",
    )

    assert last_result is Verdict.PARTIAL


@pytest.mark.perf
def test_decide_gate_red_delay_injection_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction with delay injection -- injecting delay into
    `decide` via `delayed_sync` must make the same performance assertion
    fail (budget exceeded). This proves the performance assertion actually
    measures execution time, not some other property.

    Delay is 20ms per `decide()` call. On Windows `time.process_time()` has
    ~15.6ms quantization (64 Hz clock); 5ms busy-wait registers as 0ms and
    silently falsifies the gate-red proof. 20ms reliably registers as 31.25ms
    in CPU time, exceeding the 0.1 ms budget.

    With n=3, batch=2 this completes in ~1s wall-clock."""
    slow_decide = delayed_sync(verdict_mod.decide, 0.02)
    monkeypatch.setattr(verdict_mod, "decide", slow_decide)

    issues = [_reject_issue(i) for i in range(50)]

    def _run() -> None:
        slow_decide(issues, total=1000)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _run,
            budget_ms=0.1,
            n=3,
            warmup=0,
            batch=2,
            label="decide 50 issues with 20ms delay",
        )


def test_decide_is_deterministic_across_repeated_replay() -> None:
    """Replay proof -- callers that reprocess the same batch (e.g. an
    at-least-once ingestion pipeline redelivering the same quality-check
    input) must get an identical verdict from repeated `decide()` calls on
    the same input. Hidden mutable state would show up as drift across
    replays."""
    issues = [_reject_issue(i) for i in range(50)]

    results = [decide(issues, total=1000) for _ in range(25)]

    assert all(r == results[0] for r in results)


def test_concurrent_decide_calls_are_consistent_and_thread_safe() -> None:
    """Concurrency proof -- `decide()` is a pure function with no shared
    mutable state, so concurrent calls on the same input from multiple
    threads must all agree with the single-threaded answer, catching any
    future edit that adds shared mutable state (e.g. a memoizing cache)."""
    issues = [_reject_issue(i) for i in range(50)]
    expected = decide(issues, total=1000)
    results: list[object] = [None] * 30

    def _call(i: int) -> None:
        results[i] = decide(issues, total=1000)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == expected for r in results)
