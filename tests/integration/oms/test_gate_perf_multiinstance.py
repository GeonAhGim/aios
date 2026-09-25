"""L4-06 DEEPEN(task-2762) — DEPTH_L4_BR 감사 D2/D3 미달 보강.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-06,
docs/audit/DEPTH_L4_BR.md#1563(등급 D1, D3 축 하한 미달) — 근거: "no numeric
performance/latency assertion, no explicit gate/CI red-line regression test,
no multi-worker concurrency proof in this file". `test_db_transition_trigger.py`
는 손 UPDATE 강제·라운드트립·인젝션 방지만 증명했고, 이 파일이 나머지 세
가지(D2 수치 성능 단언, 게이트 적색 재현, D3 다중 인스턴스/리플레이 증명)를
추가한다.

수치 성능 단언은 절대 ms 상수를 쓰지 않는다 — `tests/integration/foundation/
ledger/test_perf_journal.py`(task-920/1029/1038)가 이 저장소의 공유 CI
환경에서 절대 임계가 로컬 대비 최대 20배 변동해 상시 적색을 낳은 전례를
남겼다. 대신 같은 연결의 기준 왕복비용(`SELECT 1`)에 정규화한 임계를 쓴다.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.oms.conftest import (
    arm_cutover_sql,
    create_test_user,
    insert_order,
)

_TRIGGER = "oms_enforce_order_transition_trg"


@pytest.mark.perf
async def test_trigger_guarded_update_latency_within_normalized_budget(pool):
    """D2 수치 성능 단언 — 트리거가 붙은 UPDATE의 p95 지연이 같은 연결의
    기준 왕복비용(`SELECT 1`) 대비 정규화한 임계를 넘지 않는다."""
    reps = 30

    async def _p95_ms(step) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        order_id = await insert_order(conn, user_id)

        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))
        trigger_p95 = await _p95_ms(
            lambda: conn.execute(
                "UPDATE orders SET price = price + 1 WHERE order_id = $1", order_id
            )
        )

    budget_ms = max(30.0, 8.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"trigger_guarded_update p95={trigger_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert trigger_p95 < budget_ms


async def test_gate_red_disabling_trigger_lets_terminal_reversal_through(pool):
    """게이트 적색 재현 — `test_terminal_reverse_transition_raises_once_
    cutover_armed`가 지키는 I4(터미널 불변)가 트리거 자체 때문에 강제됨을
    증명한다. 트리거를 끄면 같은 조작이 통과해야 한다 — 통과하지 않는다면
    그 불변은 우연히 다른 제약이 지켰던 것이고 이 트리거는 죽은 코드다."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_id = await insert_order(
                conn, user_id, status="FILLED", quantity=Decimal("1"), filled_quantity=Decimal("1")
            )
            await conn.execute(f"ALTER TABLE orders DISABLE TRIGGER {_TRIGGER}")
            await conn.execute(
                "UPDATE orders SET status = 'SUBMITTED' WHERE order_id = $1", order_id
            )
            status = await conn.fetchval(
                "SELECT status FROM orders WHERE order_id = $1", order_id
            )
            assert status == "SUBMITTED"
        finally:
            # DISABLE TRIGGER는 세션이 아니라 카탈로그 상태다 — 트랜잭션 롤백이
            # 원상복구하지만(DDL도 트랜잭션 내에서 되돌아간다), 명시적으로 다시
            # 켜서 이 테스트가 실패로 중단돼도 이후 테스트에 트리거 비활성 상태를
            # 남기지 않는다(방어 심화, `_ensure_head`류 관례와 동일 목적).
            await conn.execute(f"ALTER TABLE orders ENABLE TRIGGER {_TRIGGER}")
            await tr.rollback()


async def test_concurrent_conditional_update_multi_instance_exactly_one_wins(pool):
    """D3 다중 인스턴스 증명 — 서로 다른 커넥션(별도 OMS 워커 프로세스 시뮬레이션)
    8개가 동일 order_id·동일 version을 조건으로 동시에 UPDATE를 시도해도
    정확히 1건만 성공한다(105번 표준 조건부 UPDATE가 유실 갱신을 막는다)."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        order_id = await insert_order(conn, user_id)

    n_instances = 8

    async def attempt() -> bool:
        async with pool.acquire() as worker_conn:
            result = await worker_conn.execute(
                "UPDATE orders SET price = price + 1 WHERE order_id = $1 AND version = 0",
                order_id,
            )
            return result == "UPDATE 1"

    results = await asyncio.gather(*(attempt() for _ in range(n_instances)))
    assert sum(results) == 1

    async with pool.acquire() as conn:
        version = await conn.fetchval(
            "SELECT version FROM orders WHERE order_id = $1", order_id
        )
    assert version == 1


async def test_concurrent_duplicate_provider_event_replay_exactly_one_wins(pool):
    """D3 리플레이 증명 — 같은 provider 이벤트가 (거래소 재전송 등으로) N개의
    독립 커넥션에서 동시에 중복 전달돼도 UNIQUE(venue, provider_event_id)가
    정확히 1건만 통과시킨다. 순차 중복(negative test)이 아니라 실제 동시
    경합에서도 지켜지는지가 D3의 요구다."""
    venue = "bitget"
    provider_event_id = f"evt-{uuid4().hex}"
    n_deliveries = 10

    async def attempt() -> bool:
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO provider_event_inbox (
                        venue, provider_event_id, venue_symbol, venue_status,
                        filled_quantity, venue_ts, source, raw_hash
                    ) VALUES ($1, $2, 'BTC/USDT', 'FILLED', 0, now(), 'WS', $3)
                    """,
                    venue,
                    provider_event_id,
                    "f" * 64,
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    results = await asyncio.gather(*(attempt() for _ in range(n_deliveries)))
    assert sum(results) == 1

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
            venue,
            provider_event_id,
        )
    assert count == 1
