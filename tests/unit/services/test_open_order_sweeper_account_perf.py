"""open_order_sweeper ACCOUNT 스코프 perf + gate-red (분할 파일).

500줄 ratchet 때문에 옮겼다(ruff format 일괄 적용 뒤 원 파일이 513줄이 됐다).

예산(PERF_BUDGET_MS)과 `_sweep_account` 헬퍼는 원 파일(test_open_order_sweeper.py)을 그대로 쓴다.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders
from tests.unit.services.conftest import _adapters, _seed_order

# 예산(100ms, 로컬 실측 ~1.6ms의 약 60배 여유)은 원 파일의 PERF_BUDGET_MS를 그대로 쓴다.
from tests.unit.services.test_open_order_sweeper import PERF_BUDGET_MS, _sweep_account


@pytest.mark.perf
async def test_account_scope_sweep_perf_budget_and_injected_delay_goes_red(pool, perf_budget):
    """Perf + gate-red — ACCOUNT sweep best-of 측정(예산은 모듈 상단 PERF_BUDGET_MS,
    로컬 실측 ~1.6ms 대비 30x 이상 여유), 10x 가상 지연 주입 시 단언이 적색이어야 한다."""
    from tests.support.perf_inject import virtual_delayed_async

    user_id = await create_test_user(pool)
    adapter = _adapters("bitget")["bitget"]

    async def _sweep():
        await _seed_order(pool, user_id)
        return await _sweep_account(pool, str(user_id), {"bitget": adapter})

    baseline = await perf_budget.best_of_async(_sweep, n=5, warmup=1)
    assert baseline.wall_ms < PERF_BUDGET_MS, perf_budget.describe(
        baseline, budget_ms=PERF_BUDGET_MS
    )

    adapter.cancel_order = virtual_delayed_async(
        adapter.cancel_order, seconds=PERF_BUDGET_MS * 10 / 1000
    )
    slow = await perf_budget.best_of_async(_sweep, n=3, warmup=0)
    assert slow.wall_ms >= PERF_BUDGET_MS * 10
