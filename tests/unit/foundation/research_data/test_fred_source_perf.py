"""RD-13 perf -- `parse_fred_observations` 처리량 예산 검증.

Split from `test_fred_source.py` (task-22133) to reduce pytest-xdist parallel
contention on the original file. The throughput test is heavy (2000 iterations)
and causes worker contention when run alongside other tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-13
DoD ("거시 시계열").
"""

from __future__ import annotations

from typing import Any

import pytest

from src.foundation.research_data.adapters.sources.fred import (
    parse_fred_observations,
)
from src.foundation.research_data.domain.macro_series import MacroFrequency
from tests.conftest import PerfBudget


def _observation(**overrides: Any) -> dict[str, Any]:
    row = {
        "realtime_start": "2024-02-20",
        "realtime_end": "2024-02-20",
        "date": "2024-01-01",
        "value": "3.5",
    }
    row.update(overrides)
    return row


# --- perf assertion (D2 floor) ----------------------------------------------


@pytest.mark.perf
def test_parse_fred_observations_meets_throughput_budget(perf_budget: PerfBudget) -> None:
    """반복 수신(폴링) 패턴에서 `parse_fred_observations`가 처리량 예산을
    지키는지 확인한다 -- `perf_budget` 픽스처로만 측정한다(raw
    `time.perf_counter()` 직접 비교 금지, perf-measurement 래칫)."""
    iterations = 2_000
    budget_ms = 3000.0  # 실측 로컬 <400ms, CI 편차 감안
    payload = {
        "observations": [_observation(date=f"2024-{(i % 12) + 1:02d}-01") for i in range(50)]
    }

    def _run() -> None:
        for _ in range(iterations):
            observations = parse_fred_observations(
                payload,
                series_id="UNRATE",
                frequency=MacroFrequency.MONTHLY,
                publication_lag_days=20,
            )
            assert len(observations) == 50

    sample = perf_budget.assert_within(_run, budget_ms=budget_ms, label="fred_parse_throughput")
    print(
        f"[RD-13 fred] {iterations} parses x 50 obs in {sample.cpu_ms:.1f}ms (budget<{budget_ms}ms)"
    )
