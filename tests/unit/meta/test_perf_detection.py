"""Negative controls for the shared mutation-check helper and static inventory."""

import pytest

from scripts.audit_perf_measure import inspect_source
from tests.support.perf_detection import assert_async_p99_ms, assert_slowdown_detected


async def noop() -> None:
    pass


@pytest.mark.parametrize(
    "kwargs",
    [{"budget_ms": 0}, {"budget_ms": 5, "samples": 1}, {"budget_ms": 5, "minimum_batch": 1}],
)
async def test_invalid_measurement_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        await assert_async_p99_ms(noop, **kwargs)


async def test_hollow_guard_rejected() -> None:
    with pytest.raises(AssertionError, match="escaped"):
        await assert_slowdown_detected(noop)


async def test_unrelated_assertion_is_not_detection() -> None:
    async def broken() -> None:
        raise AssertionError("wrong response")

    with pytest.raises(AssertionError, match="wrong response"):
        await assert_slowdown_detected(broken)


async def test_original_budget_failure_is_detection() -> None:
    async def slow() -> None:
        raise AssertionError("p99=50ms budget=5ms batch=8")

    await assert_slowdown_detected(slow)


def test_audit_marks_single_call_and_units_without_claiming_dynamic_success() -> None:
    evidence = inspect_source(
        "def test_perf(perf_budget):\n s = perf_budget.sample(f)\n assert s.cpu_ms * 1000 < 5\n"
    )
    assert evidence["single_call_lines"] == [2]
    assert evidence["rescaled_cpu_ms_lines"] == [3]
    assert evidence["gate_red_result"] == "not executed; names do not prove detection"


def test_explicit_batch_is_not_single_call() -> None:
    evidence = inspect_source("s = perf_budget.sample(f, batch=8)")
    assert evidence["single_call_lines"] == []
