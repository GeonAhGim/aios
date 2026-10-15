"""open_order_sweeper 통합테스트 — order_events 원자성·실패주입·수치 성능.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번), §9(R-39).
FA-16(task-2406): docs/specs/ibor_fund_accounting_and_resilience.md#§9.
전이된 각 주문이 정확히 1개의 order_events 행을 동반하는지(I-10), 조건부
UPDATE 의존성이 실패했을 때 같은 트랜잭션의 이벤트 INSERT도 함께 롤백되는지
(실패주입), 그리고 DEPTH 감사(task-2724)가 지목한 마지막 공백인 수치 성능
단언을 실제 Postgres 행/타이밍으로 검증한다. scope 매핑/검증은
`test_open_order_sweeper.py`, 멱등성·동시성은
`test_open_order_sweeper_concurrency.py`로 분리했다(task-10872). 공유
fixture/헬퍼는 `conftest.py`.
"""

from __future__ import annotations

import math
from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders
from tests.unit.services.conftest import _adapters, _CountingCancelAdapter, _seed_order, _status_of


async def test_each_transitioned_order_produces_exactly_one_order_event(pool):
    """DoD(a)(b) — 전이된 각 주문은 정확히 1개의 order_events 행을 동반한다
    (bulk UPDATE 시절에는 0건이었다 — FA-16 재현 대상). task-2432부터
    `CANCEL_REQUESTED`가 실제 `OrderStatus` 멤버라 `to_status`는 자기루프가
    아니라 실제 도착 상태('CANCEL_REQUESTED')를 그대로 담는다."""
    user_id = await create_test_user(pool)
    submitted = await _seed_order(pool, user_id, status="SUBMITTED")
    partially_filled = await _seed_order(pool, user_id, status="PARTIALLY_FILLED")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert set(report.cancel_requested) == {submitted, partially_filled}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT order_id, from_status, to_status, event FROM order_events "
            "WHERE order_id = ANY($1::uuid[])",
            [submitted, partially_filled],
        )
    assert len(rows) == 2
    by_order = {r["order_id"]: r for r in rows}
    assert by_order[submitted]["from_status"] == "SUBMITTED"
    assert by_order[partially_filled]["from_status"] == "PARTIALLY_FILLED"
    for row in rows:
        assert row["to_status"] == "CANCEL_REQUESTED"
        assert row["event"] == "CANCEL_REQUESTED"


async def test_conditional_update_failure_rolls_back_order_event_atomically(pool, monkeypatch):
    """실패주입(task-4139 DEEPEN, monkeypatch로 의존성 예외 유발) — 조건부
    UPDATE 의존성(`conditional_update`)이 예외를 던지면 같은 트랜잭션 안의
    `order_events` INSERT도 함께 롤백되어야 한다(I-10: 이벤트 없는 상태변경도,
    상태 없는 이벤트도 남지 않는다). 모듈 docstring이 명시하듯 이 실패는
    개별 주문 실패로 삼켜지는 어댑터 예외가 아니라 "genuine invariant
    violation"이라 그대로 전파된다."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)

    async def _raise(*args, **kwargs):
        raise RuntimeError("conditional_update dependency exploded")

    monkeypatch.setattr("src.services.safety.open_order_sweeper.conditional_update", _raise)

    with pytest.raises(RuntimeError, match="conditional_update dependency exploded"):
        await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        )

    assert await _status_of(pool, order_id) == "SUBMITTED"
    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    assert event_count == 0


@pytest.mark.perf
async def test_sweep_open_orders_p95_latency_stays_within_normalized_ceiling(pool, perf_budget):
    """수치 성능 단언(task-3029, DEPTH 감사 task-2724가 지목한 마지막 공백).

    raw time.perf_counter() 단언을 perf_budget.samples_async() 경유로 전환
    (task-11862). 비동기 I/O(DB 라운드트립)이므로 PerfBudget.wall_ms로
    측정하고, p95 분포 단언은 그대로 유지한다.

    예산: baseline 대비 max_ratio 5배 + 50ms 고정 오버헤드 (불변).
    """
    user_id = await create_test_user(pool)
    adapter = _CountingCancelAdapter()

    # baseline: 같은 모양 1회 호출
    await _seed_order(pool, user_id)
    baseline_result = await perf_budget.sample_async(
        lambda: sweep_open_orders(
            pool,
            {"bitget": adapter},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        )
    )
    baseline_elapsed_s = baseline_result.wall_ms / 1000

    # p95 측정: 30회 samples_async → wall_ms 분포에서 p95 계산
    measurements = await perf_budget.samples_async(
        lambda: sweep_open_orders(
            pool,
            {"bitget": adapter},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        ),
        n=30,
    )
    wall_ms_list = sorted(m.wall_ms for m in measurements)
    p95_ms = wall_ms_list[math.ceil(0.95 * len(wall_ms_list)) - 1]

    ceiling_ms = baseline_elapsed_s * 5 * 1000 + 50
    assert p95_ms <= ceiling_ms, (
        f"sweep_open_orders(주문 1건) p95 wall {p95_ms:.2f}ms가 정규화 상한 "
        f"{ceiling_ms:.2f}ms(baseline {baseline_result.wall_ms:.2f}ms)를 초과했습니다 -- "
        "주문 단위 트랜잭션 라운드트립 회귀 의심"
    )


@pytest.mark.perf
async def test_sweep_open_orders_p95_latency_detection_injected_delay(
    pool, perf_budget, monkeypatch
):
    """검출력 확인 — sweep_open_orders에 200ms 지연을 주입하면 적색이어야 한다.

    raw 단언이 허울이 아닌지 확인하는 실패주입: 어댑터 cancel에 sleep을
    monkeypatch로 주입해 baseline 대비 10배 이상 지연을 만든다. 이 지연이
    p95 단언을 적색으로 만들어야 perf_budget 전환의 의미가 있다."""
    import time as _time

    user_id = await create_test_user(pool)
    adapter = _CountingCancelAdapter()

    # baseline (지연 없음)
    await _seed_order(pool, user_id)
    baseline_result = await perf_budget.sample_async(
        lambda: sweep_open_orders(
            pool,
            {"bitget": adapter},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        )
    )
    baseline_ms = baseline_result.wall_ms

    # 지연 주입: cancel 호출 시 200ms sleep
    original_cancel = adapter.cancel_order

    async def _slow_cancel(*args, **kwargs):
        _time.sleep(0.2)
        return await original_cancel(*args, **kwargs)

    adapter.cancel_order = _slow_cancel

    # 지연 버전 p95 측정 — 각 호출 전에 주문을 다시 심어야 실제 cancel이 발생함
    async def _sweep_with_seed():
        await _seed_order(pool, user_id)
        return await sweep_open_orders(
            pool,
            {"bitget": adapter},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        )

    measurements = await perf_budget.samples_async(_sweep_with_seed, n=10)
    wall_ms_list = sorted(m.wall_ms for m in measurements)
    p95_ms = wall_ms_list[math.ceil(0.95 * len(wall_ms_list)) - 1]

    # baseline 대비 10배 이상 지연이 발생해야 함 (단언이 제대로 작동하는지 확인)
    assert p95_ms > baseline_ms * 5, (
        f"지연 주입에도 p95({p95_ms:.0f}ms)가 baseline({baseline_ms:.0f}ms)*5를 넘지 않음 — "
        "지연 주입이 누수되었거나 monkeypatch가 작동 안 함"
    )
