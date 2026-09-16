"""DEEPEN task-3178 — task-1189(WORM 소급 적용 + foundation_audit_event RLS
수정, commit 0e77d6b6)의 D2 하한 증빙 보강.

원 리프는 실 Postgres 통합테스트(test_db_roles.py/test_rls_legacy_not_enabled.py)
로 해피패스·negative를 이미 갖췄지만, 전부 real DB 왕복뿐이라 (1) monkeypatch
기반 실패 주입, (2) 수치 성능 단언, (3) 게이트 적색 재현이 없었다
(ADR-2026-09-09-C Decision 1). 새 기능 추가 없이 이 두 리프의 증빙만 채운다.

D3 여부: 이 리프의 spec id(L0-3/L0-5, PLT-30)는 ADR-2026-09-09-C D3 축
목록(R/L4/LA/LB/LC/FA/CM/EO/DC)에 없다 — D2 floor만 적용한다
(docs/design/INVARIANTS.md에는 감사로그 불변성을 직접 겨냥한 번호가 없고,
가장 가까운 I-04는 전략 아티팩트 대상이다).
"""

from __future__ import annotations

import time
from uuid import uuid4

import asyncpg
import pytest

from src.core.db.tenant_scope import system_transaction, tenant_transaction


async def _insert_foundation_audit_event(conn: asyncpg.Connection) -> int:
    row = await conn.fetchrow(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.deepen', gen_random_uuid(), 'test.action', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid4().int % (2**62),  # system(tenant_id IS NULL) sequence_no 유일 제약 회피용 난수
    )
    return row["id"]


async def _insert_audit_log(conn: asyncpg.Connection, action_type: str) -> int:
    row = await conn.fetchrow(
        "INSERT INTO audit_log (actor_agent, action_type, decision_data) "
        "VALUES ('test-suite', $1, '{}'::jsonb) RETURNING log_id",
        action_type,
    )
    return row["log_id"]


# --- 게이트 적색 재현: esc-ci-cbb8b9c62497 ---------------------------------


async def test_gate_red_repro_foundation_audit_event_role_system_guc(pool):
    """RED(수정 전 재현)↔GREEN(task-1189 수정)을 같은 이벤트로 대조한다.

    수정 전에는 `app.role='system'`을 바인딩하지 않고 UPDATE했다 — RLS
    정책(`tenant_id IS NULL AND current_setting('app.role', true) = 'system'`)이
    이 조건을 통과시키지 못해 모든 행을 걸러 0행 UPDATE로 조용히 끝나고, WORM
    트리거는 애초에 발동 기회조차 얻지 못한다(원래 CI가 "DID NOT RAISE"로
    적색이었던 이유 — tests/integration/test_db_roles.py::
    test_aios_app_cannot_update_foundation_audit_event가 이 GUC를 바인딩해
    고친 부분). 여기서는 그 GUC를 의도적으로 생략해 실제로 다시 적색(0행,
    예외 없음)이 재현됨을 보이고, 이어서 GREEN(GUC 바인딩) 경로가 같은 행에
    대해 진짜로 예외를 내는지 대조한다.
    """
    async with pool.acquire() as conn:
        event_id = await _insert_foundation_audit_event(conn)

        # RED — app.role='system' 바인딩 생략 (수정 전 버그 재현)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SET ROLE aios_app")
            result = await conn.execute(
                "UPDATE foundation_audit_event SET outcome = 'DENIED' WHERE id = $1",
                event_id,
            )
            assert result == "UPDATE 0", (
                "RED 재현 실패 — GUC 없이도 행이 갱신됐다면 RLS 정책 자체가 회귀한 것"
            )
        finally:
            await tx.rollback()

        # GREEN — app.role='system' 바인딩(task-1189 수정)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SET ROLE aios_app")
            await conn.execute("SELECT set_config('app.role', 'system', true)")
            with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
                await conn.execute(
                    "UPDATE foundation_audit_event SET outcome = 'DENIED' WHERE id = $1",
                    event_id,
                )
        finally:
            await tx.rollback()


async def test_gate_red_repro_enabling_rls_on_legacy_table_breaks_pool_acquire_reads(pool):
    """PLT-30 §10 리스크1 재현 — M5가 `positions`(레거시 테이블)에는 정책만
    만들고 일부러 ENABLE하지 않은 이유. 만약 ENABLE했다면 기존 40여 서비스가
    쓰는 `pool.acquire()`(트랜잭션 밖, `app.tenant_id` 미바인딩) 경로가 0행을
    받아 조용히 깨진다 — 이 테스트는 owner 권한으로 ENABLE을 흉내내(같은
    트랜잭션 안에서 롤백) 실제로 0행이 되는지 확인해, 왜 M5가 그 테이블을
    ENABLE하지 않는 쪽을 택했는지를 실측으로 증명한다.
    """
    from tests.integration.conftest import create_test_user

    user_a = await create_test_user(pool)
    strategy_id = f"rls-legacy-gate-red-{user_a.hex[:8]}"

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions "
            "(user_id, symbol, exchange, strategy_id, quantity, average_entry_price, "
            "entry_time) "
            "VALUES ($1, 'BTC/USDT', 'bitget', $2, 1, 100, now())",
            user_a,
            strategy_id,
        )

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("ALTER TABLE positions ENABLE ROW LEVEL SECURITY")
            # 테이블 소유자(현재 conn)는 RLS를 기본적으로 우회하므로(PostgreSQL
            # 원칙), 실제 서비스 경로와 같은 `aios_app`으로 전환해야 ENABLE의
            # 효과가 보인다. app.tenant_id 미바인딩(기존 서비스 경로 그대로) —
            # 정책이 있으니 ENABLE된 순간부터는 전부 걸러져야 한다.
            await conn.execute("SET ROLE aios_app")
            rows = await conn.fetch(
                "SELECT user_id FROM positions WHERE strategy_id = $1", strategy_id
            )
            assert rows == [], (
                "ENABLE ROW LEVEL SECURITY 후에도 행이 보였다면 tenant_isolation "
                "정책 자체가 사라졌거나 회귀한 것"
            )
        finally:
            await tx.rollback()

    # 롤백 후에는 실제 마이그레이션 상태(ENABLE 안 됨)로 복원돼 정상 조회된다.
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM positions WHERE strategy_id = $1", strategy_id)
    assert {r["user_id"] for r in rows} == {user_a}


# --- 실패 주입(monkeypatch) ------------------------------------------------


async def test_system_transaction_propagates_failure_without_yielding_half_bound_conn(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`system_transaction()`은 `app.tenant_id`→`app.role` 순서로 두 번
    `execute`한 뒤에야 커넥션을 yield한다. 두 번째 호출(`app.role='system'`
    바인딩)에서 인프라 예외가 나면, 그 예외를 삼키고 절반만 바인딩된
    커넥션을 호출자에게 넘기면 안 된다 — 그러면 호출자가 `app.role` 없이
    system 전용 정책(위 테스트의 GUC)을 우회/오작동시킬 수 있다.
    `asyncpg.Connection.execute`를 monkeypatch해 두 번째 호출에서만 실패를
    주입하고, `async with` 본문이 전혀 실행되지 않음을 확인한다.
    """
    role_guc_calls = 0
    original_execute = asyncpg.Connection.execute

    async def _flaky_execute(self, query, *args, **kwargs):
        nonlocal role_guc_calls
        if "app.role" in query:
            role_guc_calls += 1
            raise ConnectionResetError("simulated infra failure")
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _flaky_execute)

    body_ran = False
    with pytest.raises(ConnectionResetError):
        async with system_transaction(pool) as _conn:
            body_ran = True  # pragma: no cover — 도달하면 안 됨

    assert body_ran is False
    assert role_guc_calls == 1


async def test_tenant_transaction_propagates_failure_without_yielding_unbound_conn(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tenant_transaction()`의 유일한 `execute`(`app.tenant_id` 바인딩)에서
    인프라 예외가 나면 마찬가지로 예외가 그대로 전파돼야 한다 — 바인딩 실패를
    삼키고 미바인딩 커넥션을 넘기면 그 커넥션은 fail-closed(빈 문자열) 대신
    "GUC 자체가 없음" 상태가 되어 RLS 정책의 `current_setting(..., true)`가
    NULL을 반환하는 의도치 않은 경로를 탈 수 있다.
    """
    tenant_guc_calls = 0
    original_execute = asyncpg.Connection.execute

    async def _flaky_execute(self, query, *args, **kwargs):
        nonlocal tenant_guc_calls
        if "app.tenant_id" in query:
            tenant_guc_calls += 1
            raise ConnectionResetError("simulated infra failure")
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _flaky_execute)

    body_ran = False
    with pytest.raises(ConnectionResetError):
        async with tenant_transaction(pool, None) as _conn:
            body_ran = True  # pragma: no cover — 도달하면 안 됨

    assert body_ran is False
    assert tenant_guc_calls == 1


# --- 수치 성능 단언 ---------------------------------------------------------

_WORM_REJECT_BUDGET_MS = 20.0


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


async def _worm_reject_round_trip_latencies_ms(
    pool: asyncpg.Pool, *, iterations: int = 30
) -> list[float]:
    samples: list[float] = []
    async with pool.acquire() as conn:
        for i in range(iterations):
            log_id = await _insert_audit_log(conn, f"test.deepen.perf.{i}")
            tx = conn.transaction()
            await tx.start()
            started = time.perf_counter()
            try:
                with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
                    await conn.execute("SET ROLE aios_app")
                    await conn.execute(
                        "UPDATE audit_log SET actor_agent = 'tampered' WHERE log_id = $1",
                        log_id,
                    )
            finally:
                samples.append((time.perf_counter() - started) * 1000)
                await tx.rollback()
    return samples


async def test_worm_guard_rejection_round_trip_p95_latency_budget(pool):
    """수치 성능 단언 — `audit_log` WORM 가드(REVOKE/트리거)에 걸려 거부되는
    UPDATE 왕복은 hot write 경로(CLAUDE.md §3 standard-105) 바로 옆에서
    반복 호출될 수 있으므로 지연이 통제돼야 한다. ADR-2026-09-09-C Decision 1
    예산표에는 감사로그 가드 자체의 항목이 없어 자체 예산을 건다 — 로컬
    실측 p95 ~0.5ms(단일 라운드트립: SET ROLE + 거부된 UPDATE + 롤백,
    2026-09-17) 대비 약 40배 여유를 둔 20ms — 네트워크/CI 변동을 흡수한다."""
    samples = await _worm_reject_round_trip_latencies_ms(pool)
    p95_ms = _p95(samples)
    print(f"[task-3178] WORM 거부 왕복 p95={p95_ms:.2f}ms budget<{_WORM_REJECT_BUDGET_MS:.0f}ms")
    assert p95_ms < _WORM_REJECT_BUDGET_MS


async def test_perf_budget_gate_actually_fails_when_worm_round_trip_stalls(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현 — WORM 가드 왕복이 실제로 느려지면
    `test_worm_guard_rejection_round_trip_p95_latency_budget`과 동일한
    단언식이 진짜로 `AssertionError`를 내는지 확인한다(= 그 단언이 항상
    통과하는 tautology가 아님을 보장)."""
    import asyncio

    original_execute = asyncpg.Connection.execute

    async def _stalled_execute(self, query, *args, **kwargs):
        if query.startswith("UPDATE audit_log"):
            await asyncio.sleep(_WORM_REJECT_BUDGET_MS / 1000)
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _stalled_execute)

    samples = await _worm_reject_round_trip_latencies_ms(pool, iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _WORM_REJECT_BUDGET_MS
