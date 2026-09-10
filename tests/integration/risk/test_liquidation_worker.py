"""R-52 통합테스트 -- liquidation_executor.run_liquidation_worker_once().

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-52 (task-2358), §4
`liquidation_request` state table rows 426-435, §9 R-52 DoD (fence 변경
ABORTED, deadline 시장가 폴백 1회, `is_liquidation` 주문은 request 참조)."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.liquidation_executor import (
    _advance,
    _select_candidate,
    run_liquidation_worker_once,
)
from src.services.safety.liquidation_planning import LiquidationSeedKeyMissingError
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.performance.oms.conftest import attach_round_trip_logger


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture(scope="module", autouse=True)
async def _clear_stale_requests():
    """`_select_candidate`'s SELECT...FOR UPDATE SKIP LOCKED LIMIT 1 has no
    per-test scoping (the frozen contract has no request_id param) -- it
    picks whatever non-terminal `liquidation_request` row is globally
    oldest. Other suites in the shared TEST_DATABASE_URL (e.g.
    test_watchdog_liquidation_request.py, which predates this worker and
    never transitions the rows it creates) can leave REQUESTED rows behind
    that would otherwise silently steal every test in this module."""
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state='ABORTED', completed_at=now() "
            "WHERE state IN ('REQUESTED','PLANNED','EXECUTING')"
        )
    await pool.close()
    yield


async def _create_request(pool: asyncpg.Pool, actor: UUID, requests: list[UUID]) -> UUID:
    """§4 row 430 -- what watchdog_process._apply_decision does for LIQUIDATE,
    except scope=ACCOUNT(actor) instead of watchdog's own GLOBAL: GLOBAL has
    no scope_ref filter at all, so it would sum every other test's (and every
    unrelated test suite's) leftover positions in the shared TEST_DATABASE_URL
    into this plan. `_load_positions`/`run_liquidation_worker_once` treat
    both scopes identically -- only the SQL filter differs -- so this is
    still exercising the real ACCOUNT code path, not a shortcut."""
    repo = PostgresRiskGateRepository(pool)
    view = await activate_safety_control(
        repo,
        tenant_id=actor,
        actor_subject_id=actor,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(actor),
        reason="test",
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO liquidation_request "
            "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
            "VALUES ($1, 'ACCOUNT', $2, 'REQUESTED', 'test', $3) RETURNING id",
            view.id,
            str(actor),
            view.fence_token,
        )
    requests.append(row["id"])
    return row["id"]


async def _insert_open_position(pool: asyncpg.Pool, user_id: UUID, symbol: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', 1, 50000, now())",
            user_id,
            symbol,
        )


async def _setup(pool: asyncpg.Pool, ctx: dict, *, with_position: bool = True) -> UUID:
    """§4 GLOBAL scope has no scope_ref filter -- `_load_positions` sums every
    account's open positions *per symbol*, so a random per-test symbol is
    what actually isolates tests from each other's leftovers in the shared
    TEST_DATABASE_URL (row cleanup via `ctx`/`_cleanup` on top, belt and
    braces): no other test can ever hold a position in this test's symbol."""
    actor = await create_test_user(pool)
    ctx["actors"].append(actor)
    if with_position:
        symbol = f"TL{uuid4().hex[:10]}/USDT"
        await _insert_open_position(pool, actor, symbol)
    return await _create_request(pool, actor, ctx["requests"])


async def _request_row(pool: asyncpg.Pool, request_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM liquidation_request WHERE id = $1", request_id)


async def _slice_states(pool: asyncpg.Pool, request_id: UUID) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT state FROM liquidation_slice WHERE request_id = $1 ORDER BY seq", request_id
        )
    return [r["state"] for r in rows]


@pytest.fixture
def ctx():
    return {"actors": [], "requests": []}


@pytest.fixture(autouse=True)
async def _cleanup(pool, ctx):
    yield
    async with pool.acquire() as conn:
        for request_id in ctx["requests"]:
            await conn.execute(
                "UPDATE liquidation_slice SET order_id = NULL WHERE request_id = $1", request_id
            )
            await conn.execute("DELETE FROM orders WHERE liquidation_request_id = $1", request_id)
            await conn.execute("DELETE FROM liquidation_slice WHERE request_id = $1", request_id)
            await conn.execute("DELETE FROM liquidation_request WHERE id = $1", request_id)
        for actor in ctx["actors"]:
            await conn.execute("DELETE FROM positions WHERE user_id = $1", actor)
        await conn.execute(
            "UPDATE safety_control SET state='INACTIVE', deactivated_at=now() WHERE state='ACTIVE'"
        )


async def test_seed_key_missing_fails_closed(pool, monkeypatch):
    monkeypatch.delenv("AIOS_LIQUIDATION_SEED_KEY", raising=False)
    with pytest.raises(LiquidationSeedKeyMissingError):
        await run_liquidation_worker_once(pool, {}, now=datetime.now(timezone.utc))


async def test_no_open_positions_marks_request_done(pool, ctx):
    request_id = await _setup(pool, ctx, with_position=False)
    adapters = {"bitget": FakeExchangeAdapter()}

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "DONE"


async def test_plans_open_position_into_slices(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "PLANNED"
    assert row["seed_ref"] is not None
    states = await _slice_states(pool, request_id)
    assert len(states) >= 3
    assert all(s == "PENDING" for s in states)


async def test_fence_change_aborts_request_and_skips_slices(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    actor = ctx["actors"][0]

    # Plan it first so there are PENDING slices to abort.
    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))
    assert (await _request_row(pool, request_id))["state"] == "PLANNED"

    # A brand-new control bumps this ACCOUNT's fence past the request's snapshot.
    repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        repo,
        tenant_id=actor,
        actor_subject_id=actor,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(actor),
        reason="supersede",
    )

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "ABORTED"
    assert row["completed_at"] is not None
    assert all(s == "SKIPPED" for s in await _slice_states(pool, request_id))


async def test_due_slice_sends_liquidation_order_referencing_request(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    t0 = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED
    await run_liquidation_worker_once(pool, adapters, now=t0 + timedelta(seconds=60))  # send seq 0

    row = await _request_row(pool, request_id)
    assert row["state"] == "EXECUTING"
    states = await _slice_states(pool, request_id)
    assert states[0] == "SENT"

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT is_liquidation, liquidation_request_id, status "
            "FROM orders WHERE liquidation_request_id = $1",
            request_id,
        )
    assert order is not None
    assert order["is_liquidation"] is True
    assert order["liquidation_request_id"] == request_id
    assert order["status"] == "SUBMITTED"


async def test_deadline_sends_single_market_fallback_and_completes(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    t0 = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED
    row = await _request_row(pool, request_id)
    deadline_at = row["requested_at"] + timedelta(seconds=300)

    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=1))

    row = await _request_row(pool, request_id)
    assert row["state"] == "DONE"
    assert all(s == "SENT" for s in await _slice_states(pool, request_id))

    async with pool.acquire() as conn:
        orders = await conn.fetch(
            "SELECT order_type, quantity, is_liquidation, liquidation_request_id "
            "FROM orders WHERE liquidation_request_id = $1",
            request_id,
        )
    assert len(orders) == 1  # single consolidated fallback order, not one per slice
    assert orders[0]["order_type"] == "MARKET"
    assert orders[0]["quantity"] == Decimal("1.0000000000")
    assert orders[0]["is_liquidation"] is True


# ---- DEEPEN(task-2844) -- 동시 워커 간 중복실행 방지 실증 -------------------
# 부족했던 증빙: §5 행454(SELECT...FOR UPDATE SKIP LOCKED)가 실제 2-워커
# 동시경합으로 증명된 적이 한 번도 없었다(로직만 구현). `_select_candidate`의
# FOR UPDATE SKIP LOCKED 트랜잭션은 fetchrow 직후(같은 함수 안에서) 곧장
# 끝난다는 걸 코드에서 확인했다 -- 그 잠금은 후보를 "고르는" 짧은 순간만
# 지켜줄 뿐, 그 뒤 실제 처리(계획 수립·슬라이스 전송) 구간 전체를 막아주지는
# 않는다. 진짜 이중실행 방지는 그 아래 conditional_update(WHERE state=기대값)
# + `liquidation_slice`의 UNIQUE(request_id, seq)가 한다. 아래 테스트들은
# 이 조합이 실제 asyncio 동시성 아래에서도 버티는지 직접 증명한다.


class _GatedTickerAdapter(FakeExchangeAdapter):
    """워커 A를 가격 조회(계획 수립 안쪽)에서 묶어 두어, 그 사이 워커 B가
    같은 REQUESTED 행을 끝까지 계획할 수 있는 창을 결정론적으로 연다(시간
    기반 경합 대신 asyncio.Event로 순서를 고정 -- OMS의 `_stale_worker_
    late_write.py::_Gate`와 동일 관례, 타이밍에 취약하지 않다)."""

    def __init__(self, *, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self._started = started
        self._release = release

    async def get_ticker(self, symbol: str):
        self._started.set()
        await self._release.wait()
        return await super().get_ticker(symbol)


async def test_two_concurrent_workers_racing_same_requested_row_plan_it_exactly_once(pool, ctx):
    """D3 -- 실제 2-워커 동시경합. 워커 A가 가격 조회에서 멈춰 있는 사이
    워커 B가 같은 REQUESTED 행을 완전히 계획한다(REQUESTED -> PLANNED,
    슬라이스 INSERT). A를 풀어주면 A는 (같은 request_id로부터 유도된 같은
    HMAC seed라 결정론적으로 동일한) 슬라이스를 뒤늦게 삽입하려다
    UNIQUE(request_id, seq) 위반으로 트랜잭션 전체가 롤백된다 -- 이중 계획도
    이중 슬라이스도 남지 않는다."""
    request_id = await _setup(pool, ctx)
    started, release = asyncio.Event(), asyncio.Event()
    adapter_a = _GatedTickerAdapter(started=started, release=release)
    adapter_b = FakeExchangeAdapter()
    now = datetime.now(timezone.utc)

    task_a = asyncio.create_task(run_liquidation_worker_once(pool, {"bitget": adapter_a}, now=now))
    await asyncio.wait_for(started.wait(), timeout=5)

    # B는 A가 아직 슬라이스를 넣기 전에 같은 행을 끝까지 계획한다.
    await run_liquidation_worker_once(pool, {"bitget": adapter_b}, now=now)
    assert (await _request_row(pool, request_id))["state"] == "PLANNED"
    slice_count_after_b = len(await _slice_states(pool, request_id))

    release.set()
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await task_a

    row = await _request_row(pool, request_id)
    assert row["state"] == "PLANNED"  # A의 실패로 롤백 -- B의 결과만 남는다
    states = await _slice_states(pool, request_id)
    assert len(states) == slice_count_after_b  # 이중 삽입 없음
    assert all(s == "PENDING" for s in states)


async def test_locked_candidate_row_is_skipped_not_double_processed(pool, ctx):
    """§5 행454의 SELECT...FOR UPDATE SKIP LOCKED 자체 증명 -- 다른 트랜잭션이
    이미 이 REQUESTED 행을 FOR UPDATE로 잠그고 있으면(동시에 실행 중인 다른
    워커 인스턴스를 흉내) 이 호출은 SKIP LOCKED로 조용히 건너뛰고 아무 것도
    처리하지 않는다(sweeper의 동일 패턴 -- tests/unit/services/
    test_open_order_sweeper.py:test_toctou_race_exposes_locked_order_in_raced)."""
    request_id = await _setup(pool, ctx)

    locker_conn = await pool.acquire()
    try:
        tx = locker_conn.transaction()
        await tx.start()
        await locker_conn.fetchrow(
            "SELECT state FROM liquidation_request WHERE id = $1 FOR UPDATE", request_id
        )
        try:
            adapters = {"bitget": FakeExchangeAdapter()}
            await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))
        finally:
            await tx.rollback()
    finally:
        await pool.release(locker_conn)

    row = await _request_row(pool, request_id)
    assert row["state"] == "REQUESTED"
    assert await _slice_states(pool, request_id) == []


async def test_stale_planned_snapshot_cannot_resurrect_an_aborted_request(pool, ctx):
    """게이트 적색 재현(적대적) -- 워커 A가 PLANNED 스냅샷을 손에 쥔 채
    가격조회/네트워크 구간에서 멈춰 있는 사이, 실제로 실행된 다른 워커가
    펜스 변경으로 이 요청을 ABORTED로 확정하고 슬라이스를 전부 SKIPPED로
    돌렸다고 하자. A가 그 스냅샷 그대로 `_advance`를 이어가면, 이미
    SKIPPED된 슬라이스 중엔 PENDING이 없어 `_finish_if_drained`가 (스냅샷의)
    PLANNED -> 기대와 실제(ABORTED)가 어긋나 `ConcurrencyConflictError`를
    던진다 -- 이미 종료된 요청이 뒤늦은 쓰기로 되살아나거나 중복 주문이
    발생하지 않는다."""
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    actor = ctx["actors"][0]
    now = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=now)  # REQUESTED -> PLANNED
    stale_row = await _select_candidate(pool)
    assert stale_row is not None and stale_row["state"] == "PLANNED"

    repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        repo,
        tenant_id=actor,
        actor_subject_id=actor,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(actor),
        reason="supersede",
    )
    await run_liquidation_worker_once(pool, adapters, now=now)  # 실제 워커가 ABORT를 확정
    assert (await _request_row(pool, request_id))["state"] == "ABORTED"

    with pytest.raises(ConcurrencyConflictError):
        await _advance(pool, adapters, stale_row, now)

    row = await _request_row(pool, request_id)
    assert row["state"] == "ABORTED"  # 뒤늦은 쓰기로 되살아나지 않았다
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE liquidation_request_id = $1", request_id
        )
    assert order_count == 0  # 중복 주문 없음


async def test_place_order_exception_marks_order_and_slice_failed_not_silent_success(pool, ctx):
    """실패 주입 -- 거래소 어댑터가 예외를 던져도 워커가 죽지 않고(한 슬라이스의
    실패가 루프 전체를 막지 않는다는 모듈 계약, `_submit_order`의 BLE001
    catch), 그 슬라이스/주문이 조용히 성공(SENT)으로 남지도 않는다
    (fail-closed: FAILED로 기록)."""

    async def _boom(order):
        raise RuntimeError("exchange unreachable")

    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter(on_place_order=_boom)}
    t0 = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED
    await run_liquidation_worker_once(pool, adapters, now=t0 + timedelta(seconds=60))  # send seq 0

    states = await _slice_states(pool, request_id)
    assert states[0] == "FAILED"
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status FROM orders WHERE liquidation_request_id = $1", request_id
        )
    assert order is not None
    assert order["status"] == "FAILED"


async def test_slice_send_tick_round_trip_budget(pool, ctx):
    """성능 단언(D2) -- 슬라이스 1건을 전송하는 틱의 순차 DB 왕복 수가 고정
    예산을 넘지 않는다(재시도·불필요한 재조회가 섞여들면 회귀). 절대
    벽시계 시간은 환경(DB/CPU) 편차에 노출되므로 print만 하고 비차단으로
    남긴다(task-1038/1521 decision과 동일 관례) -- CI 차단 게이트는 왕복
    수 정확 합계. 왕복 수를 정확히 세려면 매 호출이 같은 물리 커넥션을
    돌려받아야 하므로 이 파일 공용 `pool`(max_size=8)이 아닌 전용
    단일-커넥션 풀을 쓴다."""
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    t0 = datetime.now(timezone.utc)
    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED(공용 풀)

    perf_pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
    try:
        queries = await attach_round_trip_logger(perf_pool)
        queries.clear()
        started = time.perf_counter()
        await run_liquidation_worker_once(perf_pool, adapters, now=t0 + timedelta(seconds=60))
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        states = await _slice_states(pool, request_id)
        assert states[0] == "SENT"  # 예산을 재는 틱이 실제로 슬라이스를 보냈는지 확인
        print(
            f"\nliquidation slice-send tick(real db): elapsed={elapsed_ms:.2f}ms(비차단, "
            f"환경 편차) round_trips={len(queries)}(budget==19, CI 차단)"
        )
        # 실측(2026-09-10, task-2844): 후보 SELECT tx(BEGIN/SELECT/COMMIT) +
        # due-slice tx(BEGIN/SELECT/conditional_update UPDATE x2/COMMIT) +
        # _submit_order tx(BEGIN/INSERT orders/UPDATE slice/audit INSERT/COMMIT)
        # + 커넥션 반환 시 asyncpg 세션 리셋(x N) = 19.
        assert len(queries) == 19, (
            f"슬라이스 전송 틱의 DB 왕복 수({len(queries)})가 예산(19)과 다릅니다 -- "
            "선택/전이 경로에 재시도나 추가 조회가 섞여든 회귀일 수 있습니다(리뷰 필요)."
        )
    finally:
        await perf_pool.close()
