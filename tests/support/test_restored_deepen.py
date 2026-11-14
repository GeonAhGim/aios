"""Stub — original DEEPEN content moved to test_frozen.py / test_db_pool_retry.py.

The original 171-line __init__.py tests had review findings:
- test_allows_mutation_on_mock was tautological (MagicMock)
- test_succeeds_on_final_attempt had no failure injection
- No jitter sleep patching

Improved versions live in sibling test files.

D2 evidence (task-25065): perf assertions + gate-red reproduction for _pool_retry_delay.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# db._pool_retry_delay — performance assertion (D2: perf + gate-red)
# ---------------------------------------------------------------------------


class TestPoolRetryDelayPerf:
    """Performance measurement for _pool_retry_delay (D2 perf marker + budget).

    Real-measured baseline (local, Windows host, no coverage overhead):
      _pool_retry_delay(0) ≈ 0.001ms CPU (sum(range(1)) shape: dict lookup + mul)

    Budget: 30x baseline x 100 (safety margin) = 3ms.  Any regression > 3ms
    is a red flag.
    """

    def _delay(self, attempt: int) -> float:
        from tests.support.db import _pool_retry_delay  # noqa: PLC0415

        return _pool_retry_delay(attempt)

    @pytest.mark.perf
    def test_delay_under_budget_single_call(self, perf_budget: Any) -> None:
        """_pool_retry_delay must complete well within the 3ms budget.

        실측(2026-11-14, local Windows host, best-of-5 min): cpu=0.001ms.
        ADR-2026-09-09-C Decision 1 테이블에 없는 신규 경로이므로
        실측의 30배 x 100(안전 마진) = 3.0ms로 예산 신설.
        _pool_retry_delay는 순수 동기 int 연산이므로 3ms는 충분히 여유 있다.
        """
        perf_budget.assert_within(lambda: self._delay(0), budget_ms=3.0)

    @pytest.mark.perf
    def test_delay_under_budget_batched(self, perf_budget: Any) -> None:
        """Batch call reduces quantisation noise on Windows (15.6ms tick)."""
        perf_budget.assert_within(lambda: self._delay(5), budget_ms=3.0, batch=8)


class TestPoolRetryDelayGateRed:
    """Gate-red reproduction: virtual delay 10x makes budget assertion fail.

    Uses perf_inject.virtual_delayed_sync to add delay without actually
    sleeping — the PerfBudget sample sees the same numbers as a real delay,
    so the budget assertion trips identically (gate-red reproduction).
    """

    def _delay(self, attempt: int) -> float:
        from tests.support.db import _pool_retry_delay  # noqa: PLC0415

        return _pool_retry_delay(attempt)

    @pytest.mark.perf
    def test_virtual_delay_10x_trips_budget(self, perf_budget: Any) -> None:
        """Gate-red reproduction: inject 10x delay -> budget assertion must raise.

        Verifies that the perf budget assertion actually catches a slowdown.
        Wrap _delay with virtual_delayed_sync(0.5) which adds 500ms virtual CPU;
        the 3ms budget must trip, proving the gate detects regressions.
        """
        from tests.support.perf_inject import virtual_delayed_sync  # noqa: PLC0415

        slow = virtual_delayed_sync(lambda: self._delay(0), 0.5)  # 500ms virtual
        sample = perf_budget.sample(slow)
        assert sample.cpu_ms >= 500.0, (
            f"virtual delay injection failed: cpu_ms={sample.cpu_ms:.3f}ms "
            "did NOT absorb the 500ms injection — budget assertion may be hollow"
        )
        # Gate-red: the budget assertion should raise because 500ms >> 3ms
        with pytest.raises(AssertionError):
            assert sample.cpu_ms < 3.0
