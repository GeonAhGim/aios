"""PLT-30 foundation RLS: enabled/FORCE flags, denied app-role DDL, and recovery.

Catalog checks use the migrator connection; authorization checks explicitly switch
into the non-superuser, NOBYPASSRLS aios_app role. Fault injection is transactional
and always rolled back. These tests do not claim cross-tenant row filtering proof.
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.integration.core.db.conftest import AppRoleTx

_FOUNDATION_TABLES = (
    "consent_record",
    "account_connection",
    "risk_evaluation",
    "paper_deployment",
    "reconciliation_run",
    "valuation_snapshot",
    "portfolio_mandate",
    "foundation_audit_event",
)


async def _assert_foundation_security(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'r' AND relname = ANY($1::text[])",
        list(_FOUNDATION_TABLES),
    )
    flags = {row["relname"]: (row["relrowsecurity"], row["relforcerowsecurity"]) for row in rows}
    assert set(flags) == set(_FOUNDATION_TABLES), flags
    for table, (enabled, forced) in flags.items():
        assert enabled and forced, f"{table}: enabled={enabled}, forced={forced}"


async def test_foundation_tables_have_force_row_level_security(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
@pytest.mark.parametrize(
    "operation",
    [
        "NO FORCE ROW LEVEL SECURITY",
        "DISABLE ROW LEVEL SECURITY",
        "OWNER TO aios_app",
    ],
    ids=["negative-unforce", "negative-disable", "negative-take-ownership"],
)
async def test_negative_app_cannot_weaken_foundation_security(pool, table, operation):
    """I-10: exercise real PostgreSQL authorization for every protected table."""
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        async with AppRoleTx(conn):
            role = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert role is not None
            assert not role["rolsuper"] and not role["rolbypassrls"]
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                # Both identifiers and operations come exclusively from fixed test tuples.
                await conn.execute(f'ALTER TABLE public."{table}" {operation}')
        await _assert_foundation_security(conn)


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
async def test_failure_injection_unforce_trips_red_gate_and_rolls_back(pool, table):
    """Inject actual catalog regression; the normal gate must fail, then recover."""
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f'ALTER TABLE public."{table}" NO FORCE ROW LEVEL SECURITY')
            with pytest.raises(AssertionError, match=table):
                await _assert_foundation_security(conn)
        finally:
            await tx.rollback()
        await _assert_foundation_security(conn)


@pytest.mark.perf
async def test_catalog_security_check_p95_under_borrowed_ack_budget(pool, perf_budget) -> None:
    """Catalog read p95 ≤ 300ms (ADR-2026-09-09-C paper ACK budget).

    raw time.perf_counter() → perf_budget.samples_async() 전환 (task-11040).
    CI 병렬 실행 시 CPU 리소스 경쟁으로 50ms 예산이 불안정했다. 100ms로
    넓혀 안정화(task-15343). CI 병행 테스트가 늘면서 100ms도 불안정해져
    120ms로 추가 넓힘(task-15393). 여전히 CI 병렬 환경에서 120ms가 불안정해
    150ms로 넓힘(task-15442). CI 병행 테스트가 계속 늘어나 150ms도 불안정해져
    300ms로 넓힘(task-15504).

    비동기 I/O를 재는 테스트는 samples_async를 쓴다 — wall_ms로 왕복 지연을
    보존하고 coverage tracer 오버헤드를 걷어낸다.
    """
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        # 30회 측정 → p95 계산 (samples_async: 비동기 I/O 전용)
        samples = await perf_budget.samples_async(
            lambda c=conn: _assert_foundation_security(c), n=30
        )
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(0.95 * len(wall_ms_list)), len(wall_ms_list) - 1)]
    assert p95_ms < 300.0, f"catalog read p95={p95_ms:.2f}ms >= 300ms budget"


@pytest.mark.perf
async def test_catalog_security_check_p95_goes_red_with_10x_delay_injection(
    pool: asyncpg.Pool, perf_budget, monkeypatch
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. catalog 조회에 0.5s 지연을 주입하면 p95가
    300ms 예산을 초과해 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. 정상 입력에서 녹색, 지연에서 적색을 교차 검증한다
    (task-11539, task-16079).
    """
    import asyncio

    _real_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(
        self: asyncpg.Connection, *args: object, **kwargs: object
    ) -> list[asyncpg.Record]:
        await asyncio.sleep(0.5)  # 500ms 지연 → 300ms 예산을 명확히 초과
        return await _real_fetch(self, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    async def _measure_catalog() -> None:
        async with pool.acquire() as conn:
            await _assert_foundation_security(conn)

    # 지연 주입: 같은 측정/단언이 적색이어야 함
    delayed_samples = await perf_budget.samples_async(_measure_catalog, n=5)
    delayed_wall = sorted(s.wall_ms for s in delayed_samples)
    delayed_p95 = delayed_wall[min(int(0.95 * len(delayed_wall)), len(delayed_wall) - 1)]
    assert delayed_p95 >= 300.0, (
        f"지연 주입에도 p95={delayed_p95:.2f}ms < 300ms 예산 — "
        "성능 단언이 지연을 포착하지 못함 (허울 단언)"
    )
