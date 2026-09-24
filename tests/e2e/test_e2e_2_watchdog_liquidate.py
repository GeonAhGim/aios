"""E2E-2 -- 워치독 LIQUIDATE: 시장 전반 급변 -> decide() -> liquidation_request
-> 실행 루프(청산 워커) 소비 -> WORM 감사기록.

Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3
("깊이 미판정 리프를 개별 재QA하는 대신 E2E 시나리오로 대체 검증").

이 시나리오가 커버하는 명세 리프:
- FD-9.1/9.2 (`src/core/safety/watchdog.py`) -- equity peak-to-current 손실률
  롤링 윈도우 + HALT/LIQUIDATE/NORMAL 판정.
- FD-9.3 (`src/core/safety/split_brain.py`) -- DB-only 고립 장애 진단, 강제조치
  보류(HALT 강등).
- R-49 (`src/core/safety/market_correlation.py`) -- basket 상관 판정
  (market_wide_correlated).
- R-51 (`src/watchdog_process.py::run_one_cycle`, migration `e5a8c5d4f6b7`) --
  LIQUIDATE 판정 -> `safety_control` + `liquidation_request` REQUESTED INSERT,
  fence 전파.
- R-40 (`src/services/safety/kill_switch_service.py`) -- 유일한
  `INSERT INTO safety_control` 진입점(I3), activate() 감사이벤트 기록.
- R-52 (`src/services/safety/liquidation_executor.py`,
  `src/services/safety/liquidation_planning.py`) -- 실행 루프(청산 워커)가
  REQUESTED 행을 실제로 소비해 PLANNED -> EXECUTING/시장가 폴백 -> DONE/PARTIAL로
  전이.
- L0-3/L0-5 (`src/core/db/append_only.py`, `src/core/db/roles.py`, migration
  `4a1d0c0de001`) -- `foundation_audit_event`의 WORM 강제(REVOKE + 트리거),
  `aios_app` 역할도 UPDATE 불가.

기존 `tests/integration/risk/test_watchdog_market_wide_liquidation_e2e.py`
(watchdog LIQUIDATE -> REQUESTED -> 워커가 PLANNED로 전이시키는 지점까지)와
`tests/integration/risk/test_liquidation_worker.py`(청산 워커 단독, 거부/폴백
실패 주입)를 중복하지 않는다 -- 이 파일은 그 두 경로를 이어 붙여 (1) 워치독이
만든 REQUESTED 행이 실제로 DONE/PARTIAL까지 완주하는지, (2) 그 경로 전체에서
`foundation_audit_event`에 실제 WORM(append-only) 감사기록이 남고 위조가
거부되는지를 실증한다. `tests/e2e/conftest.py`의 `pool` 픽스처를 그대로
재사용한다."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogService
from src.services.safety.liquidation_executor import run_liquidation_worker_once
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _LastAppliedAction,
    _LatestExchangeHealth,
    build_kill_switch_service,
    run_one_cycle,
)
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


# tests/e2e/conftest.py의 `pool`을 그대로 쓴다 (min/max_size 조정이 필요 없다 --
# 이 파일은 동시성 부하 테스트가 아니다).


@pytest.fixture
def kill_switch(pool):
    return build_kill_switch_service(pool)


@pytest.fixture(scope="module", autouse=True)
async def _clear_stale_liquidation_requests():
    """`_select_candidate`의 SELECT...FOR UPDATE SKIP LOCKED LIMIT 1은 전역에서
    가장 오래된 non-terminal 행을 고른다 -- 같은 공유 TEST_DATABASE_URL의 다른
    스위트가 남긴 REQUESTED 행이 있으면 이 모듈의 워커 호출이 그걸 훔쳐간다
    (test_liquidation_worker.py / test_watchdog_market_wide_liquidation_e2e.py와
    동일 관례)."""
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state='ABORTED', completed_at=now() "
            "WHERE state IN ('REQUESTED','PLANNED','EXECUTING')"
        )
    await pool.close()
    yield


@pytest.fixture(autouse=True)
async def _cleanup_watchdog_state(pool):
    """이 파일이 만드는 GLOBAL safety_control/liquidation_request가 공유
    TEST_DATABASE_URL의 다른 risk-gate 테스트를 오염시키지 않도록,
    WATCHDOG_SYSTEM_ACTOR_ID로 만들어진 것만 정리한다."""
    yield
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE liquidation_request SET state = 'ABORTED', completed_at = now() "
            "WHERE safety_control_id IN "
            "(SELECT id FROM safety_control WHERE actor_subject_id = $1) "
            "AND state IN ('REQUESTED', 'PLANNED', 'EXECUTING')",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )


async def _insert_open_position(
    pool: asyncpg.Pool, user_id: UUID, symbol: str, *, quantity: Decimal = Decimal("1")
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', $3, 50000, now())",
            user_id,
            symbol,
            quantity,
        )


async def _latest_liquidation_request(
    pool: asyncpg.Pool, *, since: datetime
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT lr.* FROM liquidation_request lr "
            "JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1 AND lr.requested_at > $2 "
            "ORDER BY lr.requested_at DESC LIMIT 1",
            WATCHDOG_SYSTEM_ACTOR_ID,
            since,
        )


async def _market_wide_basket() -> dict[str, Decimal]:
    # 4개 중 3개(과반)가 임계(3%)를 넘겨 하락 -- 시장 전체 급변으로 판정돼야 한다.
    return {
        "BTC/USDT": Decimal("-9"),
        "ETH/USDT": Decimal("-8"),
        "SOL/USDT": Decimal("-9"),
        "XRP/USDT": Decimal("0.1"),
    }


async def _run_liquidate_cycle(
    pool: asyncpg.Pool,
    kill_switch,
    *,
    heartbeat_path,
    check_exchange,
    check_db,
    split_brain: SplitBrainDiagnostics | None = None,
) -> asyncpg.Record:
    """LIQUIDATE 발동에 필요한 최소 2사이클(peak 기록 -> 급락 판정)을 돌리고,
    이번 테스트가 만든 liquidation_request 행을 반환한다."""
    write_heartbeat(heartbeat_path)
    equity_readings = iter([Decimal("10000"), Decimal("1000")])  # peak -> 90% 손실

    async def compute_equity() -> Decimal:
        return next(equity_readings)

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat_path,
    )
    split_brain = split_brain or SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    cycle_kwargs = {
        "check_exchange": check_exchange,
        "check_db": check_db,
        "exchange_health_cache": exchange_health_cache,
        "kill_switch": kill_switch,
        "last_action": last_action,
        "get_basket_returns": _market_wide_basket,
    }

    test_start = datetime.now(timezone.utc)
    # 1회차 -- peak 기록, 손실률 0% -> NORMAL.
    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)
    # 2회차 -- 90% 손실 + market-wide 급변 basket -> LIQUIDATE 발동(조건에 따라).
    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)

    return await _latest_liquidation_request(pool, since=test_start)


# ---- 본선 경로 -- LIQUIDATE -> 실행 루프 완주 -> WORM 감사기록 --------------


async def test_watchdog_liquidate_drains_through_execution_loop_and_records_worm_audit(
    pool: asyncpg.Pool, kill_switch, tmp_path
) -> None:
    """전체 경로 실증: 시장 전체 급변 basket + 계좌 손실 -> decide()가 실제로
    LIQUIDATE 판정 -> `liquidation_request` REQUESTED INSERT -> 실행 루프
    (`run_liquidation_worker_once`)가 그 행을 PLANNED -> 데드라인 시장가 폴백 ->
    DONE까지 실제로 소비 -> `foundation_audit_event`에 WORM 감사기록이 실제로
    남고(aios_app으로도 위조 불가) 남는다."""
    user_id = await create_test_user(pool)
    symbol = f"E2E2{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
    )
    assert request_row is not None, (
        "market_wide_correlated 급락에도 liquidation_request가 생기지 않음"
    )
    assert request_row["state"] == "REQUESTED"
    control_id = request_row["safety_control_id"]

    # 실행 루프(청산 워커)가 이 요청을 실제로 소비 -- REQUESTED -> PLANNED, 그리고
    # 데드라인(기본 300초) 경과 시점으로 한 번 더 돌려 시장가 폴백으로 완주시킨다
    # (개별 슬라이스 하나씩 보내는 경로는 test_liquidation_worker.py가 이미
    # 커버하므로, 여기서는 "실행 루프가 워치독의 요청을 끝까지 소비한다"는
    # 경계만 증명한다).
    adapter = FakeExchangeAdapter()
    adapters = {"bitget": adapter}
    t0 = request_row["requested_at"]
    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED

    plan_row = await pool.fetchrow(
        "SELECT plan FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    import json as _json

    deadline_offset = _json.loads(plan_row["plan"])["plan"]["deadline_offset_sec"]
    deadline_at = t0 + timedelta(seconds=deadline_offset)

    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=1))

    consumed_row = await pool.fetchrow(
        "SELECT state FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    assert consumed_row["state"] == "DONE", (
        "실행 루프가 워치독 발행 liquidation_request를 끝까지 소비하지 않음"
    )

    # 수치 단언(Decimal 정확값) -- 우리 심볼의 주문 수량이 등록한 포지션 수량과
    # 정확히 일치한다(다른 테스트의 잔여 포지션과 섞이지 않았음을 함께 증명).
    order = await pool.fetchrow(
        "SELECT order_type, quantity, is_liquidation, liquidation_request_id, status "
        "FROM orders WHERE liquidation_request_id = $1 AND symbol = $2",
        request_row["id"],
        symbol,
    )
    assert order is not None
    assert order["order_type"] == "MARKET"
    assert order["quantity"] == Decimal("1.0000000000")
    assert order["is_liquidation"] is True
    assert order["status"] == "SUBMITTED"
    assert adapter.place_order_call_count >= 1

    # ---- WORM 감사기록 실증 ----------------------------------------------
    audit_row = await pool.fetchrow(
        "SELECT id, action, outcome, classification, tenant_id, payload "
        "FROM foundation_audit_event WHERE aggregate_type = 'safety_control' "
        "AND aggregate_id = $1 ORDER BY sequence_no DESC LIMIT 1",
        control_id,
    )
    assert audit_row is not None, "safety_control 활성화가 감사기록을 남기지 않음"
    assert audit_row["action"] == "safety_control_activated"
    assert audit_row["outcome"] == "SUCCESS"
    assert audit_row["tenant_id"] is None  # GLOBAL scope -- system 이벤트
    import json

    payload = json.loads(audit_row["payload"])
    assert payload["scope"] == "GLOBAL"
    assert payload["reason"] == "market_wide_correlated_loss"

    # 위조 시도가 실제로 거부됨(WORM) -- L0-3/L0-5. `aios_app`(런타임 애플리케이션
    # 역할)으로도 UPDATE가 불가능해야 한다.
    raw_conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
            async with raw_conn.transaction():
                await raw_conn.execute("SET ROLE aios_app")
                await raw_conn.execute("SELECT set_config('app.role', 'system', true)")
                await raw_conn.execute(
                    "UPDATE foundation_audit_event SET outcome = 'DENIED' WHERE id = $1",
                    audit_row["id"],
                )
        if isinstance(exc_info.value, asyncpg.RaiseError):
            assert "append-only violation" in str(exc_info.value)
    finally:
        await raw_conn.close()

    # 위조가 실제로 반영되지 않았음을 다시 읽어 확인한다.
    unchanged = await pool.fetchrow(
        "SELECT outcome FROM foundation_audit_event WHERE id = $1", audit_row["id"]
    )
    assert unchanged["outcome"] == "SUCCESS"


# ---- 실패 주입 #1 -- 거부(rejection): 거래소 어댑터가 청산 주문을 거부 -------


async def test_exchange_rejection_during_liquidation_still_completes_and_records_audit(
    pool: asyncpg.Pool, kill_switch, tmp_path
) -> None:
    """실패 주입(거부) -- 워치독이 발행한 LIQUIDATE 요청을 실행 루프가 소비하는
    도중 거래소 어댑터가 주문을 거부(예외)해도, 워커가 죽지 않고(다른 슬라이스
    처리를 막지 않음) 그 주문/슬라이스가 조용히 성공으로 위장되지 않으며(FAILED로
    기록), `liquidation_request`는 PARTIAL로 정직하게 남는다. `safety_control`
    활성화 감사기록은 거래소 거부 여부와 무관하게 그대로 남는다(감사는 실행 결과가
    아니라 "통제를 걸었다"는 사실 자체를 증명한다)."""

    async def _reject(order):
        raise RuntimeError("exchange rejected liquidation order")

    user_id = await create_test_user(pool)
    symbol = f"E2E2R{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("2"))

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=check_db,
    )
    assert request_row is not None
    control_id = request_row["safety_control_id"]

    adapters = {"bitget": FakeExchangeAdapter(on_place_order=_reject)}
    t0 = request_row["requested_at"]
    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED

    plan_row = await pool.fetchrow(
        "SELECT plan FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    import json as _json

    deadline_offset = _json.loads(plan_row["plan"])["plan"]["deadline_offset_sec"]
    deadline_at = t0 + timedelta(seconds=deadline_offset)

    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=1))

    consumed_row = await pool.fetchrow(
        "SELECT state FROM liquidation_request WHERE id = $1", request_row["id"]
    )
    assert consumed_row["state"] == "PARTIAL", (
        "거래소 거부가 조용히 DONE으로 위장됨 -- fail-closed 위반"
    )

    order = await pool.fetchrow(
        "SELECT status, quantity FROM orders WHERE liquidation_request_id = $1 AND symbol = $2",
        request_row["id"],
        symbol,
    )
    assert order is not None
    assert order["status"] == "FAILED"
    assert order["quantity"] == Decimal("2.0000000000")

    audit_row = await pool.fetchrow(
        "SELECT action FROM foundation_audit_event WHERE aggregate_type = 'safety_control' "
        "AND aggregate_id = $1",
        control_id,
    )
    assert audit_row is not None
    assert audit_row["action"] == "safety_control_activated"


# ---- 실패 주입 #2 -- 타임아웃: 거래소 헬스체크가 타임아웃되어도 강제조치는 진행 --


async def test_exchange_health_check_timeout_does_not_block_forced_liquidation(
    pool: asyncpg.Pool, kill_switch, tmp_path
) -> None:
    """실패 주입(타임아웃) -- Split-Brain의 거래소 헬스체크가 실제로 타임아웃되어도
    (`asyncio.wait_for`가 실제 시간 경과로 TimeoutError를 던짐), 그 자체는
    DB-only 고립 장애가 아니므로(§FD-9.3 diagnosis=APPLY_WATCHDOG_DECISION) 계좌
    손실이 시장 전체 급변과 상관돼 있다면 LIQUIDATE는 그대로 발동해야 한다 --
    "거래소 상태 확인이 느리다"는 사실 하나가 강제청산을 막는 우회 경로가 되면
    안 된다(fail-closed)."""

    async def slow_check_exchange() -> bool:
        await asyncio.sleep(0.2)
        return True

    async def check_db() -> bool:
        return True

    user_id = await create_test_user(pool)
    symbol = f"E2E2T{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    split_brain = SplitBrainDiagnostics(
        entry_confirm_seconds=0.0,
        recovery_confirm_seconds=0.0,
        check_timeout_seconds=0.01,
    )

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=slow_check_exchange,
        check_db=check_db,
        split_brain=split_brain,
    )

    assert request_row is not None, (
        "거래소 헬스체크 타임아웃이 강제청산 발동을 막아버림 -- 우회 경로 발생"
    )
    assert request_row["state"] == "REQUESTED"


# ---- 실패 주입 #3 -- 불일치: DB 고립 장애 진단은 강제청산을 보류시킨다 --------


async def test_db_isolated_failure_mismatch_suppresses_forced_liquidation(
    pool: asyncpg.Pool, kill_switch, tmp_path
) -> None:
    """실패 주입(불일치) -- decide()의 로컬 판정(손실률+시장 전체 급변 상관)은
    LIQUIDATE를 가리키지만, Split-Brain이 "DB만 단독 장애"로 진단하면(거래소는
    정상 응답, 메인 프로세스도 정상 -- 즉 로컬 판정과 인프라 신호가 불일치)
    `liquidation_request`를 만들지 않는다(§FD-9.3 -- DB가 거짓 상태를 반환하고
    있을 가능성이 있는 동안은 거래소 응답을 잠정 진실로 삼고 강제조치를 보류)."""

    async def check_exchange() -> bool:
        return True

    async def failing_check_db() -> bool:
        return False

    user_id = await create_test_user(pool)
    symbol = f"E2E2M{uuid4().hex[:8]}/USDT"
    await _insert_open_position(pool, user_id, symbol, quantity=Decimal("1"))

    split_brain = SplitBrainDiagnostics(
        entry_confirm_seconds=0.0,
        recovery_confirm_seconds=0.0,
    )

    request_row = await _run_liquidate_cycle(
        pool,
        kill_switch,
        heartbeat_path=tmp_path / "hb",
        check_exchange=check_exchange,
        check_db=failing_check_db,
        split_brain=split_brain,
    )

    assert request_row is None, (
        "DB 고립 장애 진단(불일치)에도 liquidation_request가 생성됨 -- "
        "FD-9.3 강제조치 보류 회귀"
    )

    async with pool.acquire() as conn:
        active_control = await conn.fetchval(
            "SELECT COUNT(*) FROM safety_control WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    assert active_control == 0
