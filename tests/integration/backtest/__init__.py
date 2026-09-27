"""BT-19 / I-05: adversarial parity checks (task-8427).

Explicitly collect this package file as required by the DEEPEN task.
"""
from __future__ import annotations

from time import perf_counter

import pytest

from src.foundation.backtest.application import parity_harness as harness


def _traces():
    # Local import keeps package initialization independent of its test module.
    from tests.integration.backtest.test_parity_harness import (
        _PAPER_TRACE,
        _matching_backtest_fills,
    )

    return list(_PAPER_TRACE), _matching_backtest_fills()


def test_negative_missing_entire_replay_is_rejected() -> None:
    paper, _ = _traces()
    report = harness.check_parity(paper, [])
    assert not report.is_match
    assert report.first_divergence == harness.Divergence(0, "__length__", "3", "0")
    with pytest.raises(harness.ParityMismatchError) as caught:
        report.raise_if_mismatch()
    assert caught.value.report is report


def test_negative_duplicate_replay_fill_is_rejected() -> None:
    paper, replay = _traces()
    replay.append(replay[-1])
    report = harness.check_parity(paper, replay)
    assert not report.is_match
    assert report.first_divergence == harness.Divergence(3, "__length__", "3", "4")
    with pytest.raises(harness.ParityMismatchError, match="index 3"):
        report.raise_if_mismatch()


def test_negative_reordered_fills_are_rejected() -> None:
    paper, replay = _traces()
    replay[0], replay[1] = replay[1], replay[0]
    report = harness.check_parity(paper, replay)
    assert not report.is_match
    assert report.first_divergence == harness.Divergence(0, "quantity", "1", "2")
    with pytest.raises(harness.ParityMismatchError, match="quantity"):
        report.raise_if_mismatch()


@pytest.mark.parametrize(
    "dependency", ["paper_fill_to_comparable", "backtest_fill_to_comparable"]
)
def test_failure_injection_normalization_error_propagates(
    monkeypatch: pytest.MonkeyPatch, dependency: str
) -> None:
    paper, replay = _traces()
    failure = RuntimeError("injected normalization failure")
    calls = []

    def fail_on_second_fill(fill):
        calls.append(fill)
        if len(calls) == 2:
            raise failure
        return original(fill)

    original = getattr(harness, dependency)
    monkeypatch.setattr(harness, dependency, fail_on_second_fill)
    with pytest.raises(RuntimeError, match="injected normalization failure") as caught:
        harness.check_parity(paper, replay)
    assert caught.value is failure
    assert len(calls) == 2


@pytest.mark.perf
def test_parity_month_m1_performance_budget() -> None:
    # ADR-2026-09-09-C: backtest one month M1 / one symbol <= 3 seconds.
    # This measures only the parity stage, not the complete backtest pipeline.
    paper, replay = _traces()
    paper = paper * 14_400
    replay = replay * 14_400
    started = perf_counter()
    report = harness.check_parity(paper, replay)
    elapsed = perf_counter() - started
    report.raise_if_mismatch()
    assert report.paper_fill_count == report.backtest_fill_count == 43_200
    assert elapsed < 3.0, f"parity stage exceeded monthly backtest budget: {elapsed:.3f}s"
