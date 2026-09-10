"""legacy_execution_pauser 통합테스트 — 실제 TEST_DATABASE_URL 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8 (R-38). 5개 SafetyScope
매핑, 행별 조건부 UPDATE, 타 테넌트 미영향, 멱등성을 실제 Postgres 행으로
검증한다 — 조건절이 맞는지는 순수 로직만으로는 증명되지 않는다.

DEEPEN(task-2823, docs/audit/DEPTH_R_EO.md leaf 1222): D1이었던 이 리프에
성능 단언·게이트 적색 재현·kill-switch 동시성 경합 증거를 추가해 D2/D3를
채운다(negative≥3·실패주입·성능단언·게이트 적색 재현은 이미 존재/아래에서
보강, 다중 인스턴스 증명은 동시성 테스트로 겸한다)."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

import src.services.safety.legacy_execution_pauser as pauser_mod
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import (
    MalformedScopeRefError,
    UnmappedSafetyScopeError,
    pause_executions_for_scope,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


async def _seed_execution(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    exchange: str = "bitget",
    status: str = "RUNNING",
) -> int:
    strategy_id = f"pauser-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, $4::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', $5)
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
            status,
        )
    return row["id"]


async def _status_of(pool: asyncpg.Pool, execution_id: int) -> tuple[str, str | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row is not None
    return row["status"], row["paused_by"]


async def test_global_scope_pauses_every_running_execution_across_tenants(pool):
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, user_a)
    exec_b = await _seed_execution(pool, user_b, exchange="binance")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.GLOBAL, "", control_id=uuid4()
        )

    assert set(paused) >= {exec_a, exec_b}
    assert await _status_of(pool, exec_a) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, exec_b) == ("PAUSED", "SAFETY_LAYER")


async def test_provider_scope_pauses_only_matching_exchange(pool):
    user_id = await create_test_user(pool)
    bitget_exec = await _seed_execution(pool, user_id, exchange="bitget")
    binance_exec = await _seed_execution(pool, user_id, exchange="binance")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.PROVIDER, "bitget", control_id=uuid4()
        )

    # `bitget_exec in paused`(not `==`)다 — PROVIDER scope는 테넌트로 좁혀지지
    # 않아, 같은 워커 DB에서 먼저 실행된 다른 테스트가 남긴 RUNNING 상태의
    # bitget 실행이 있으면 그것도 함께 매칭된다(§3.8 표대로 정상 동작). 이
    # 테스트가 검증할 것은 "bitget은 맞고 binance는 아니다"이지 전체 목록의
    # 배타적 동일성이 아니다.
    assert bitget_exec in paused
    assert binance_exec not in paused
    assert await _status_of(pool, bitget_exec) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, binance_exec) == ("RUNNING", None)


async def test_tenant_scope_does_not_affect_other_tenants_execution(pool):
    """negative test — 타 테넌트 execution 미영향(DoD 필수 항목)."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, tenant_a)
    exec_b = await _seed_execution(pool, tenant_b)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(tenant_a), control_id=uuid4()
        )

    assert paused == [exec_a]
    assert await _status_of(pool, exec_a) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, exec_b) == ("RUNNING", None)


async def test_account_scope_pauses_only_that_accounts_execution(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, tenant_a)
    exec_b = await _seed_execution(pool, tenant_b)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.ACCOUNT, str(tenant_a), control_id=uuid4()
        )

    assert paused == [exec_a]
    assert await _status_of(pool, exec_b) == ("RUNNING", None)


async def test_strategy_deployment_exec_prefix_pauses_only_that_execution(pool):
    user_id = await create_test_user(pool)
    target = await _seed_execution(pool, user_id)
    other = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.STRATEGY_DEPLOYMENT, f"exec:{target}", control_id=uuid4()
        )

    assert paused == [target]
    assert await _status_of(pool, other) == ("RUNNING", None)


async def test_strategy_deployment_dep_prefix_is_paper_control_target_not_legacy(pool):
    """§3.8 표: STRATEGY_DEPLOYMENT의 dep:<uuid>는 paper_control.deployment_id
    대상이다 — legacy strategy_executions에는 대응 행이 없으므로 0건이
    정답이다(매핑 누락이 아니라 명시적 무관)."""
    user_id = await create_test_user(pool)
    running = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.STRATEGY_DEPLOYMENT, f"dep:{uuid4()}", control_id=uuid4()
        )

    assert paused == []
    assert await _status_of(pool, running) == ("RUNNING", None)


async def test_pausing_an_already_paused_execution_is_idempotent(pool):
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id, status="PAUSED")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )

    assert paused == []
    assert await _status_of(pool, exec_id) == ("PAUSED", None)


async def test_repeated_call_after_first_pause_returns_empty_second_time(pool):
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        first = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )
        second = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )

    assert first == [exec_id]
    assert second == []


async def test_unmapped_scope_raises_instead_of_silently_matching_zero_rows(pool):
    async with pool.acquire() as conn:
        with pytest.raises(UnmappedSafetyScopeError):
            await pause_executions_for_scope(
                conn, "BOGUS_SCOPE", "irrelevant", control_id=uuid4()  # type: ignore[arg-type]
            )


async def test_tenant_scope_rejects_non_uuid_scope_ref(pool):
    async with pool.acquire() as conn:
        with pytest.raises(MalformedScopeRefError):
            await pause_executions_for_scope(
                conn, SafetyScope.TENANT, "not-a-uuid", control_id=uuid4()
            )


async def test_strategy_deployment_rejects_malformed_scope_ref(pool):
    async with pool.acquire() as conn:
        with pytest.raises(MalformedScopeRefError):
            await pause_executions_for_scope(
                conn, SafetyScope.STRATEGY_DEPLOYMENT, "garbage", control_id=uuid4()
            )


async def test_aborted_transaction_propagates_instead_of_appearing_as_zero_matches(pool):
    """실패 주입 — 같은 커넥션의 앞선 문장이 실패해 트랜잭션이 이미 aborted
    상태면(커넥션 재사용 버그의 흔한 형태), 이 함수는 그 실패를 조용히
    삼켜 "0건 정지"로 위장하지 않고 asyncpg 예외를 그대로 전파해야 한다 —
    kill switch fan-out이 "정지 성공(0건)"과 "정지 시도 자체가 실패"를
    구분하지 못하면 안전 게이트가 소리 없이 무력화된다."""
    user_id = await create_test_user(pool)
    conn = await pool.acquire()
    try:
        tx = conn.transaction()
        await tx.start()
        with pytest.raises(asyncpg.UndefinedColumnError):
            await conn.execute("SELECT this_column_does_not_exist FROM strategy_executions")
        with pytest.raises(asyncpg.InFailedSQLTransactionError):
            await pause_executions_for_scope(
                conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
            )
        await tx.rollback()
    finally:
        await pool.release(conn)


async def test_pause_executions_completes_within_single_round_trip_latency_bound(pool):
    """성능 단언 — 매칭 50건 + 비매칭 50건이 섞여 있어도 단일 UPDATE...
    RETURNING 왕복 하나로 끝난다(행별 개별 쿼리로 퇴화하는 회귀를 방지).
    느슨한 상한(500ms)은 flakiness 회피용이며 명백한 회귀만 잡는다(다른
    DEEPEN 리프의 성능 단언과 동일 기준)."""
    user_id = await create_test_user(pool)
    other_user = await create_test_user(pool)
    for _ in range(50):
        await _seed_execution(pool, user_id)
    for _ in range(50):
        await _seed_execution(pool, other_user)

    async with pool.acquire() as conn:
        start = time.monotonic()
        paused = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )
        elapsed = time.monotonic() - start

    assert len(paused) == 50
    assert elapsed < 0.5


async def test_concurrent_overlapping_scope_kill_switches_pause_each_execution_exactly_once(
    pool,
):
    """동시성/kill-switch 경합 — 실제 운영에서는 관리자의 수동 GLOBAL kill
    switch와 워치독의 자동 TENANT kill switch가 동시에 발동할 수 있다(kill
    switch는 본질적으로 동시성 민감 — DEPTH 감사가 지적한 누락 항목). 두
    스코프가 같은 실행 행을 겹쳐 대상으로 해도 조건부 UPDATE(`WHERE
    status='RUNNING'`)의 행 잠금 덕에 각 행은 어느 한쪽에서만 잡히고,
    합쳐서 중복도 누락도 없이 정확히 1회씩만 정지된다(서로 다른 커넥션
    2개가 동시에 호출 — 다중 인스턴스 증명 겸함)."""
    tenant_a = await create_test_user(pool)
    exec_1 = await _seed_execution(pool, tenant_a)
    exec_2 = await _seed_execution(pool, tenant_a)

    async def _tenant_call() -> list[int]:
        async with pool.acquire() as conn:
            return await pause_executions_for_scope(
                conn, SafetyScope.TENANT, str(tenant_a), control_id=uuid4()
            )

    async def _global_call() -> list[int]:
        async with pool.acquire() as conn:
            return await pause_executions_for_scope(
                conn, SafetyScope.GLOBAL, "", control_id=uuid4()
            )

    tenant_result, global_result = await asyncio.gather(_tenant_call(), _global_call())

    # GLOBAL은 공유 테스트 DB의 다른 실행까지 쓸어갈 수 있으므로(기존
    # GLOBAL 테스트와 동일 이유) 이 테스트가 만든 두 행에 대해서만 검사한다.
    combined = list(tenant_result) + [i for i in global_result if i in (exec_1, exec_2)]
    assert sorted(combined) == sorted([exec_1, exec_2])
    assert await _status_of(pool, exec_1) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, exec_2) == ("PAUSED", "SAFETY_LAYER")


async def test_regression_confirms_suite_would_catch_missing_tenant_filter(pool, monkeypatch):
    """게이트 적색 재현 — TENANT 조건이 실수로 GLOBAL과 같은 'TRUE'로
    퇴화하면(오픈오더 스위퍼가 FA-16에서 실제로 겪은 "조건 누락"과 같은
    계열의 배선 결함) 이 스위트가 실제로 실패(RED)함을 먼저 증명한 뒤,
    원래 구현으로 되돌려 통과(GREEN)를 재확인한다 — "테스트가 있다"가
    아니라 "테스트가 실제로 이 결함을 잡는다" 자체를 증명한다."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await _seed_execution(pool, tenant_a)
    exec_b = await _seed_execution(pool, tenant_b)

    monkeypatch.setattr(pauser_mod, "_condition_for", lambda scope, scope_ref: ("TRUE", []))
    async with pool.acquire() as conn:
        broken = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(tenant_a), control_id=uuid4()
        )
    # 결함 주입 상태 — 다른 테넌트까지 오염된다(RED 재현).
    assert exec_b in broken
    monkeypatch.undo()

    # 원상복구 후: 새 실행으로 실제 구현이 격리를 지킴을 재확인한다(GREEN).
    exec_c = await _seed_execution(pool, tenant_a)
    exec_d = await _seed_execution(pool, tenant_b)
    async with pool.acquire() as conn:
        fixed = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(tenant_a), control_id=uuid4()
        )
    assert fixed == [exec_c]
    assert await _status_of(pool, exec_d) == ("RUNNING", None)
