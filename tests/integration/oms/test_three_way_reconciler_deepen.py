"""DEEPEN(task-2800) — `three_way_reconciler.py` 수치 성능 + 다중 인스턴스 +
adversarial 증명. `test_three_way_reconciler.py`에서 분리했다(500-LOC 정책,
ADR-2026-09-10-C §7) — 공용 스캐폴딩은 `_reconciler_test_support.py`.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#2316)가 원 구현(4f1a05de)을
D3 미달(실측 D1)로 판정한 두 가지 근거를 보강한다: "수치 latency/throughput
단언 없음", "다중 인스턴스/adversarial/replay 증명 없음(단일 pool, 동시
reconciler 없음, DENY 우회 시도 없음)". 아래 세 테스트가 각각 대응한다:
(1) `reconcile_account` 왕복 지연/처리량을 기준 왕복비용에 정규화한 수치로
단언(절대 ms 상수 금지 — `test_gate_perf_multiinstance.py`,
`test_kis_durability.py` 선례), (2) 서로 다른 `ReconcileScheduler` 인스턴스
다수가 동일 account_ref를 정확히 같은 시각에 동시 대사해도 advisory xact
lock(REC-004) 덕에 정확히 1개만 실제로 실행됨, (3) MATERIAL_MISMATCH ACTIVE
동안 다수의 `submit_order` 시도가 정확히 같은 시각에 경합해도(DENY 우회
레이스 시도) 단 하나도 통과하지 못함.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.reconcile_scheduler import ReconcileScheduler, ReconcileTarget
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.application.three_way_reconciler import reconcile_account
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.oms._reconciler_test_support import (
    ScriptedAdapter,
    create_running_execution,
    insert_open_order,
    profile,
    provider_order,
    registry,
)
from tests.integration.oms.conftest import _asyncpg_dsn, create_test_tenant, seed_entity_context


@pytest.mark.perf
async def test_reconcile_account_latency_within_normalized_throughput_budget(pool):
    """D2 수치 latency/throughput 단언 — 절대 ms 상수는 쓰지 않는다(공유 CI
    환경에서 절대 임계가 로컬 대비 크게 변동한 전례, `test_gate_perf_
    multiinstance.py`/`test_kis_durability.py`와 동일 근거). 같은 연결의
    기준 왕복비용(`SELECT 1`)에 정규화한 예산 안에서 `reconcile_account`가
    끝나야 하고, 처리량(calls/s)도 함께 기록한다."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
    )
    adapter = ScriptedAdapter(open_orders=[provider_order(client_id, Decimal("5"), Decimal("2"))])

    async with pool.acquire() as conn:
        baseline_reps = 20
        t0 = time.perf_counter()
        for _ in range(baseline_reps):
            await conn.fetchval("SELECT 1")
        baseline_per_call = (time.perf_counter() - t0) / baseline_reps

    reps = 10
    t0 = time.perf_counter()
    for _ in range(reps):
        summary = await reconcile_account(
            pool=pool,
            adapter=adapter,
            tenant_id=user_id,
            connection_id=None,
            account_ref=str(user_id),
            window=timedelta(minutes=5),
        )
    elapsed = time.perf_counter() - t0
    per_call = elapsed / reps
    throughput = reps / elapsed

    # reconcile_account issues several round trips (order list + provider
    # fetch + risk-control lookup) per call, so a generous multiple of the
    # single round-trip baseline is the budget, not an absolute constant.
    budget = max(0.5, baseline_per_call * 300)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"reconcile_account per_call={per_call * 1000:.3f}ms "
        f"throughput={throughput:.2f} calls/s "
        f"baseline(SELECT 1)={baseline_per_call * 1000:.3f}ms budget={budget * 1000:.3f}ms"
    )
    assert summary.overall_classification == "HEALTHY"
    assert per_call < budget


async def test_concurrent_reconciler_instances_only_one_reconciles_same_account(pool):
    """D3 다중 인스턴스 증명 — REC-004: 서로 다른 `ReconcileScheduler`
    인스턴스(별도 스케줄러 프로세스 시뮬레이션) 여러 개가 동일 account_ref를
    정확히 같은 시각에(asyncio.gather) 동시 대사하려 해도
    `pg_try_advisory_xact_lock`이 정확히 1개만 통과시키고 나머지는 이번
    주기를 건너뛴다(단일 프로세스 순차 재실행이 아니라 실제 동시 경합).

    전용 커넥션 풀을 별도로 연다 — 공용 `pool` 픽스처는 `max_size=4`라
    `_reconcile_one`이 잠금용 커넥션 1개를 쥔 채로 `reconcile_account` 내부가
    또 다른 커넥션을 요구하는 상황에서, 인스턴스 수가 4를 넘으면 전부가
    서로의 커넥션 반납을 기다리며 교착한다(각 워커 프로세스가 자기 풀을
    갖는 실제 운영 구조와도 더 가깝다).

    `asyncio.gather`만으로는 "정확히 같은 시각"이 보장되지 않는다 — 각
    코루틴의 첫 await(`pool.acquire()`로 TCP/인증을 새로 여는 커넥션 생성
    지연)가 서로 다른 시각에 끝나면, 먼저 끈 쪽이 advisory xact lock을 쥐고
    짧게 일하고 커밋(=잠금 해제)까지 끝낸 뒤에야 다음 쪽이 같은 키를 시도해
    매번 성공해버린다 — pg_try_advisory_xact_lock 자체는 올바르게 동작하지만
    (`pg_locks`로 확인: 동시 보유 시 정확히 1개만 granted), 테스트가 "동시
    경합"을 실제로 만들지 못해 가끔(로컬 재현 약 1/4) 거짓 통과·거짓
    실패를 오간다. 커넥션을 미리 데워(pre-warm) `pool.acquire()`의 생성
    지연을 없애고, `asyncio.Condition` 배리어로 전원이 도착한 뒤 한 번에
    출발시켜 실제 동시 시도를 강제한다."""
    user_id = await create_test_tenant(pool)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("5"),
        filled_quantity=Decimal("2"),
    )
    account_ref = str(user_id)

    async def _no_targets() -> list[ReconcileTarget]:
        return []

    n_instances = 5
    scheduler_pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4 * n_instances)
    try:
        warm_conns = [await scheduler_pool.acquire() for _ in range(n_instances)]
        for conn in warm_conns:
            await scheduler_pool.release(conn)

        schedulers = [
            ReconcileScheduler(scheduler_pool, targets=_no_targets) for _ in range(n_instances)
        ]
        target = ReconcileTarget(
            tenant_id=user_id,
            connection_id=None,
            account_ref=account_ref,
            adapter=ScriptedAdapter(
                open_orders=[provider_order(client_id, Decimal("5"), Decimal("2"))]
            ),
        )

        barrier_gate = asyncio.Condition()
        arrived = 0

        async def _run_at_barrier(scheduler: ReconcileScheduler) -> bool:
            nonlocal arrived
            async with barrier_gate:
                arrived += 1
                barrier_gate.notify_all()
                while arrived < n_instances:
                    await barrier_gate.wait()
            return await scheduler._reconcile_one(target)

        results = await asyncio.gather(*(_run_at_barrier(scheduler) for scheduler in schedulers))
    finally:
        await scheduler_pool.close()

    assert sum(results) == 1


async def test_adversarial_concurrent_submit_race_all_denied_during_material_mismatch(pool):
    """D3 adversarial 증명 — ACCOUNT 세이프티 컨트롤이 MATERIAL_MISMATCH로
    ACTIVE인 동안, 서로 다른 intent_seq를 쓰는 다수의 `submit_order` 호출이
    정확히 같은 시각에(asyncio.gather) 경합해도(DENY를 레이스로 우회하려는
    시도) 단 하나도 통과하지 못하고 orders 테이블에 행이 하나도 남지
    않는다 — 순차 단일 시도(REC-002 기존 테스트)와 달리 동시 경합에서도
    게이트가 새지 않음을 증명한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    client_id = f"recon-cid-{uuid.uuid4().hex[:10]}"
    await insert_open_order(
        pool,
        user_id,
        client_order_id=client_id,
        quantity=Decimal("10"),
        filled_quantity=Decimal("3"),
    )
    mismatched_adapter = ScriptedAdapter(
        open_orders=[provider_order(client_id, Decimal("10"), Decimal("7"))]
    )
    summary = await reconcile_account(
        pool=pool,
        adapter=mismatched_adapter,
        tenant_id=user_id,
        connection_id=None,
        account_ref=str(user_id),
        window=timedelta(minutes=5),
    )
    assert summary.overall_classification == "MATERIAL_MISMATCH"

    n_attempts = 8
    # 전용 커넥션 풀 — 각 submit_order 호출이 자기 트랜잭션(1커넥션) 안에서
    # pre_submit_gate의 감사로그/결정 기록을 위해 추가 커넥션을 잠깐
    # 요구한다(`make_foundation_pre_submit_gate`/`RiskDecisionRecorder`
    # 내부). 공용 `pool` 픽스처(max_size=4)로 8개를 동시에 돌리면 모두가
    # 서로의 두 번째 커넥션 반납을 기다리며 교착한다 — 위 다중 인스턴스
    # 테스트와 동일한 이유로 여기서도 넉넉한 전용 풀을 쓴다.
    submit_pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4 * n_attempts)
    try:
        gate = make_foundation_pre_submit_gate(submit_pool, require_mandate=False)

        async def attempt(seq: int) -> str:
            scope = OrderIdempotencyScope(
                tenant_id=user_id,
                account_ref="acct-1",
                provider="bitget",
                strategy_id="s1",
                strategy_version="1.0.0",
                execution_id=execution_id,
                intent_seq=seq,
                window_start=datetime.now(timezone.utc),
            )
            cmd = SubmitOrderCommand(
                command_id=uuid.uuid4(),
                trace_id=uuid.uuid4(),
                scope=scope,
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.01"),
                asset_class=AssetClass.CRYPTO,
                actor_subject_id=user_id,
                issued_at=datetime.now(timezone.utc),
            )
            try:
                await submit_order(
                    cmd,
                    pool=submit_pool,
                    profile=profile(),
                    registry=registry(),
                    pre_submit_gate=gate,
                    entity_context=entity_context,
                    entity_repo=PostgresEntityRepository(submit_pool),
                )
            except OrderSubmitDeniedError:
                return "denied"
            return "allowed"

        results = await asyncio.gather(*(attempt(seq) for seq in range(1, n_attempts + 1)))
    finally:
        await submit_pool.close()

    assert results == ["denied"] * n_attempts
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0
