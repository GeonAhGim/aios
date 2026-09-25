"""PLT-30 — foundation 8 테이블 RLS 강제 + `tenant_transaction`/
`system_transaction` GUC 바인딩.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M5,
§9 PLT-30 DoD("WHERE 없는 SELECT가 0행, aios_app role로 교차 테넌트 접근
차단"), §8 `test_rls_scoping.py` 예시.

대표로 `consent_record`(일반 8개 중 하나)와 `foundation_audit_event`(NULL
tenant_id 예외를 갖는 유일한 테이블)를 검증한다 — 나머지 6개는 같은 M5
정책 생성 함수로 만들어진 동일한 형태의 정책이라 회귀 위험이 이 두 케이스와
다르지 않다.

FA-0a batch A(task-1814, ccfb229d760d)가 두 테이블의 tenant_id FK를
`users(user_id)`에서 `tenant(id)`로 옮긴 뒤로는, `create_test_user`가
만든 사용자에는 대응하는 `tenant` 행이 없어(PLT-26 백필은 그 리비전
시점에 이미 있던 사용자만 대상) FK 위반이 난다 — 실제 INSERT가 필요한
테스트는 `create_test_tenant`로 시드한다.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.tenant_scope import system_transaction, tenant_transaction
from tests.foundation.integration.trust.conftest import create_disclosure, unique_purpose
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.integration.core.db.conftest import AppRoleTx

# 예산표(ADR-2026-09-09-C Decision 1)에 RLS 스코프 SELECT 전용 항목은 없다 —
# SET ROLE + GUC 바인딩 + 정책 평가를 포함한 단일 실DB 왕복이라는 점에서
# "주문 제출→ACK p95 50ms(paper)"를 가장 가까운 유사 항목으로 차용한다
# (task-3160/3162/3168/3169 DEEPEN과 동일 차용 근거).
_RLS_SELECT_P95_BUDGET_MS = 50.0


async def _seed_consent(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    purpose = unique_purpose()
    disclosure_id = await create_disclosure(pool, purpose=purpose)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
            "VALUES ($1, $1, $2, $3, 1)",
            tenant_id,
            purpose,
            disclosure_id,
        )


async def _seed_audit_event(pool: asyncpg.Pool, tenant_id: UUID | None, sequence_no: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO foundation_audit_event "
            "(tenant_id, sequence_no, aggregate_type, aggregate_id, action, outcome, "
            "trace_id, payload_hash, payload, event_hash) "
            "VALUES ($1, $2, 'test.aggregate', gen_random_uuid(), 'test.action', "
            "'SUCCESS', gen_random_uuid(), 'hash', '{}'::jsonb, 'hash')",
            tenant_id,
            sequence_no,
        )


def _sequence_no() -> int:
    return uuid4().int % 1_000_000_000


async def test_tenant_transaction_binds_app_tenant_id_guc(pool):
    tenant_a = await create_test_user(pool)
    async with tenant_transaction(pool, tenant_a) as conn:
        value = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert value == str(tenant_a)


async def test_tenant_transaction_with_none_binds_empty_string(pool):
    async with tenant_transaction(pool, None) as conn:
        value = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert value == ""


async def test_system_transaction_binds_role_system(pool):
    async with system_transaction(pool) as conn:
        role = await conn.fetchval("SELECT current_setting('app.role', true)")
        tenant = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert role == "system"
    assert tenant == ""


async def test_select_without_where_returns_only_bound_tenant(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_a)
    await _seed_consent(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM consent_record")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_unbound_transaction_returns_nothing(pool):
    tenant_a = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch("SELECT 1 FROM consent_record")

    assert rows == []


async def test_insert_for_other_tenant_is_rejected(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    disclosure_id = await create_disclosure(pool, purpose=unique_purpose())

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO consent_record "
                "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
                "VALUES ($1, $1, 'other-tenant-purpose', $2, 1)",
                tenant_b,
                disclosure_id,
            )


async def test_update_moving_row_to_other_tenant_is_rejected(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "UPDATE consent_record SET tenant_id = $1 WHERE tenant_id = $2",
                tenant_b,
                tenant_a,
            )


async def test_delete_for_other_tenant_affects_no_rows(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        result = await conn.execute("DELETE FROM consent_record WHERE tenant_id = $1", tenant_b)

    assert result == "DELETE 0"


async def test_tenant_transaction_binding_failure_raises_without_yielding_connection(
    monkeypatch: pytest.MonkeyPatch, pool
):
    """실패주입: `set_config` 호출이 예외를 내면 [[tenant_transaction]]이 그
    예외를 그대로 전파해야 한다 — GUC 바인딩 없이 커넥션을 넘겨 RLS 없이
    쿼리가 실행되는 fail-open 경로가 있으면 안 된다(fail-closed 기본,
    CLAUDE.md §3)."""
    tenant_a = await create_test_tenant(pool)

    original_execute = asyncpg.Connection.execute

    async def _raise_on_set_config(self, query, *args, **kwargs):
        if "set_config" in query:
            raise RuntimeError("injected GUC binding failure")
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _raise_on_set_config)

    with pytest.raises(RuntimeError, match="injected GUC binding failure"):
        async with tenant_transaction(pool, tenant_a) as conn:
            await conn.fetch("SELECT 1")


async def test_system_role_reads_null_tenant_audit_event_only(pool):
    tenant_a = await create_test_tenant(pool)
    await _seed_audit_event(pool, tenant_a, _sequence_no())
    await _seed_audit_event(pool, None, _sequence_no())

    async with pool.acquire() as conn, AppRoleTx(conn, system=True):
        rows = await conn.fetch("SELECT tenant_id FROM foundation_audit_event")

    assert rows
    assert {r["tenant_id"] for r in rows} == {None}


async def test_ordinary_tenant_binding_excludes_null_tenant_audit_event(pool):
    tenant_a = await create_test_tenant(pool)
    await _seed_audit_event(pool, tenant_a, _sequence_no())
    await _seed_audit_event(pool, None, _sequence_no())

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM foundation_audit_event")

    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def _rls_select_p95_ms(pool: asyncpg.Pool, tenant_id: UUID, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_id):
            await conn.fetch("SELECT tenant_id FROM consent_record")
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_rls_scoped_select_p95_under_borrowed_order_ack_budget(pool):
    """수치 성능 단언: RLS로 스코프된 SELECT(SET ROLE + GUC 바인딩 + 정책
    평가를 포함한 단일 실DB 왕복)는 전용 예산 항목이 없어 "주문 제출→ACK
    p95 50ms(paper)"를 자체 예산으로 차용한다(task-3160/3162/3168/3169
    DEEPEN과 동일 차용 근거). 30회 반복 p95를 그 예산 내로 단언한다."""
    tenant_a = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_a)

    p95_ms = await _rls_select_p95_ms(pool, tenant_a, n=30)

    assert p95_ms < _RLS_SELECT_P95_BUDGET_MS


async def test_rls_scoped_select_budget_gate_fails_on_injected_regression(
    monkeypatch: pytest.MonkeyPatch, pool
):
    """실패 주입 + 게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지
    확인한다 — asyncpg.Connection.fetch에 60ms 인위 지연을 주입해, 같은
    측정 로직이 실제로 AssertionError를 내는지 본다(tautology 아님을 증명).
    alembic upgrade/downgrade 왕복(test_rls_legacy_not_enabled.py)은 스키마
    존재 여부만 확인할 뿐 수치 성능 게이트가 실제로 적색이 되는지는
    증명하지 않으므로 이 요건을 대신하지 않는다."""
    tenant_a = await create_test_tenant(pool)
    await _seed_consent(pool, tenant_a)

    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self, *args, **kwargs):
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _rls_select_p95_ms(pool, tenant_a, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _RLS_SELECT_P95_BUDGET_MS
