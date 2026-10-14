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
import time
from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
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
async def test_sweep_open_orders_p95_latency_stays_within_normalized_ceiling(pool):
    """수치 성능 단언(task-3029, DEPTH 감사 task-2724가 지목한 마지막 공백).
    단일 취소대상 주문에 대한 `sweep_open_orders` 1회 호출은 후보 선별 SELECT
    2회 + 주문당 트랜잭션(FOR UPDATE SKIP LOCKED 잠금 -> order_events INSERT ->
    조건부 UPDATE) 1회 + 어댑터 cancel 1회로 라운드트립 수가 고정되어 있어야
    한다 — task-2406의 원래 결함(단일 bulk UPDATE)이 되돌아오거나 주문 단위
    루프에 회귀(예: 후보마다 추가 조회가 붙는 것)가 생기면 이 비용이 자란다.

    공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신,
    같은 모양(주문 1건 대상 sweep)의 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(task-3004/3009 선례와 동일 패턴)."""
    user_id = await create_test_user(pool)
    adapter = _CountingCancelAdapter()

    await _seed_order(pool, user_id)
    baseline_start = time.perf_counter()
    await sweep_open_orders(
        pool,
        {"bitget": adapter},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )
    baseline_elapsed = time.perf_counter() - baseline_start

    samples: list[float] = []
    for _ in range(30):
        await _seed_order(pool, user_id)
        start = time.perf_counter()
        await sweep_open_orders(
            pool,
            {"bitget": adapter},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        )
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"sweep_open_orders(주문 1건) p95 지연 {p95:.4f}s가 정규화 상한 "
        f"{ceiling:.4f}s(baseline {baseline_elapsed:.4f}s)를 초과했습니다 -- "
        f"주문 단위 트랜잭션 라운드트립 회귀 의심"
    )
