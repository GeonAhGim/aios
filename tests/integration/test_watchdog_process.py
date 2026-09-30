"""9.1 통합테스트 — watchdog_process.py 적용 로직. 실제 dev DB 대상.

실제로 OS 프로세스를 kill해서 완료조건을 검증하는 건 배포 전 수동/CI
검증 단계 몫(9.7 시뮬레이터와 동일 성격, 20.1-A Go/No-Go 게이트)이라
여기서는 stale heartbeat 파일로 "메인 프로세스가 멎었다"를 시뮬레이션해
동일한 감지→판정→실제 DB 조치 경로를 검증한다(watchdog.py 기존
단위테스트도 실제 프로세스 kill 대신 파일 조작으로 검증하는 것과 동일
패턴)."""

import asyncio
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogAction, WatchdogDecision, WatchdogService, decide
from src.services.safety.watchdog_apply import apply_decision
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _LastAppliedAction,
    _LatestExchangeHealth,
    build_kill_switch_service,
    compute_system_equity,
    run_one_cycle,
)
from tests.integration.conftest import create_test_user


async def _empty_basket_returns() -> dict[str, Decimal]:
    """Most tests here aren't exercising the RTF-03 griefing-defense basket
    wiring — an empty basket (`< min_symbols`) makes `is_market_wide_move`
    return None, the same safe-side value `run_one_cycle` always used before
    the basket was wired in."""
    return {}


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def kill_switch(pool):
    return build_kill_switch_service(pool)


@pytest.fixture(autouse=True)
async def _deactivate_watchdog_controls_after(pool):
    """이 파일의 여러 테스트가 apply_decision/run_one_cycle을 통해
    WATCHDOG_SYSTEM_ACTOR_ID로 GLOBAL safety_control을 만든다 — ACTIVE로
    남으면 공유 테스트 DB의 다른 risk-gate 테스트가 RISK_KILL_SWITCH_ACTIVE_
    GLOBAL로 오염된다. 이 파일이 만든 통제만 지운다(actor_subject_id로 한정)."""
    yield
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )


async def _create_running_execution(pool: asyncpg.Pool, user_id) -> int:
    strategy_id = f"watchdog-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _make_cycle_deps(heartbeat: Path, compute_equity, *, fast_hysteresis: bool = False):
    """run_one_cycle 호출에 필요한 service/split_brain/exchange_health_cache 세 쌍을
    묶어준다 — 아래 여러 테스트가 health_check=exchange_health_cache.get 연결만 다른
    채 반복하던 보일러플레이트를 제거."""
    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat,
    )
    split_brain = (
        SplitBrainDiagnostics(entry_confirm_seconds=0.01, recovery_confirm_seconds=0.01)
        if fast_hysteresis
        else SplitBrainDiagnostics()
    )
    return service, split_brain, exchange_health_cache


async def _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db):
    service, split_brain, exchange_health_cache = deps
    await run_one_cycle(
        pool,
        service,
        split_brain,
        check_exchange=check_exchange,
        check_db=check_db,
        exchange_health_cache=exchange_health_cache,
        kill_switch=kill_switch,
        last_action=last_action,
        get_basket_returns=_empty_basket_returns,
    )


async def test_apply_decision_pauses_running_executions(pool, kill_switch):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    await apply_decision(
        pool,
        WatchdogDecision(action=WatchdogAction.HALT, reason="main_process_unresponsive"),
        kill_switch,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_apply_decision_records_audit_log(pool, kill_switch):
    user_id = await create_test_user(pool)
    await _create_running_execution(pool, user_id)

    await apply_decision(
        pool,
        WatchdogDecision(action=WatchdogAction.LIQUIDATE, reason="market_wide_correlated_loss"),
        kill_switch,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT action_type, decision_data FROM audit_log "
            "WHERE action_type = 'watchdog.decision.applied' ORDER BY created_at DESC LIMIT 1"
        )
    assert row is not None
    data = json.loads(row["decision_data"])
    assert data["action"] == "LIQUIDATE"
    assert data["reason"] == "market_wide_correlated_loss"


async def test_stale_heartbeat_leads_to_halt_and_real_pause(pool, kill_switch, tmp_path):
    """FD-9.1 완료조건 재현 — 메인 프로세스가 멎으면(heartbeat 미갱신) Watchdog가
    unresponsive_sec 상승을 관측하고, FD-9.2 판정을 거쳐 실제로 실행을 멈춘다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    heartbeat = tmp_path / "main_process.heartbeat"
    write_heartbeat(heartbeat)
    heartbeat.write_text(str(time.time() - 60))  # 60초 전 = 죽은 메인 프로세스 시뮬레이션

    async def compute_equity() -> Decimal:
        return Decimal("0")

    async def health_check() -> bool:
        return True

    service = WatchdogService(
        compute_equity=compute_equity, health_check=health_check, heartbeat_path=heartbeat
    )
    snapshot = await service.take_snapshot()
    assert snapshot.unresponsive_sec >= 60

    decision = decide(snapshot, market_wide_correlated=None)
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "main_process_unresponsive"

    await apply_decision(pool, decision, kill_switch)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_run_one_cycle_suppresses_action_on_db_isolated_failure(pool, kill_switch, tmp_path):
    """FD-9.3 완료조건 — "DB만 강제로 차단했을 때 강제청산이 발동하지 않고
    신규주문만 보류되는지 확인". 여기서는 실제로 DB 연결을 끊을 수 없으니
    (그러면 검증용 SELECT도 못 함) check_db 콜백만 가짜로 "끊겼다"고
    보고하게 만들어 진단 로직 자체를 검증한다 — decide()는 HALT를
    내리는데도(고립된 손실) 최종적으로 실행에 아무 조치가 안 가야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)

    async def compute_equity() -> Decimal:
        return Decimal("8000")

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return False  # DB만 끊긴 것으로 진단 유도

    # 실제 5분 대신 즉시 히스테리시스가 확정되도록 임계값을 짧게.
    deps = _make_cycle_deps(heartbeat, compute_equity, fast_hysteresis=True)
    last_action = _LastAppliedAction()

    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)
    await asyncio.sleep(0.02)  # entry_confirm_seconds 경과시켜 히스테리시스 확정
    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "RUNNING"
    assert row["paused_by"] is None


async def test_run_one_cycle_applies_action_when_not_db_isolated(pool, kill_switch, tmp_path):
    """대조군 — DB는 멀쩡하고 메인 프로세스만 응답불능이면(Split-Brain
    진단상 DB_ISOLATED_FAILURE가 아님) 평소대로 강제조치가 적용돼야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    heartbeat = tmp_path / "hb"
    heartbeat.write_text(str(time.time() - 60))  # 응답불능 시뮬레이션

    async def compute_equity() -> Decimal:
        return Decimal("10000")

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    deps = _make_cycle_deps(heartbeat, compute_equity, fast_hysteresis=True)
    last_action = _LastAppliedAction()

    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)
    await asyncio.sleep(0.02)
    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_run_one_cycle_calls_check_exchange_exactly_once(pool, kill_switch, tmp_path):
    """docs/RED_TEAM_FINDINGS.md #06 회귀 — take_snapshot()의 health_check와
    split_brain.diagnose()의 check_exchange가 각자 실제 API를 호출하면
    사이클당 2회 중복 호출됐다. 캐시(_LatestExchangeHealth)로 사이클당
    정확히 1회만 호출되는지 확인한다."""
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    call_count = 0

    async def compute_equity() -> Decimal:
        return Decimal("10000")

    async def check_exchange() -> bool:
        nonlocal call_count
        call_count += 1
        return True

    async def check_db() -> bool:
        return True

    deps = _make_cycle_deps(heartbeat, compute_equity)

    await _run_cycle(pool, deps, kill_switch, _LastAppliedAction(), check_exchange, check_db)

    assert call_count == 1


async def test_write_heartbeat_leaves_no_temp_file_and_content_is_valid(tmp_path):
    """docs/RED_TEAM_FINDINGS.md #07 회귀 — 임시파일+os.replace 패턴으로
    바뀐 뒤에도 최종 파일 내용이 그대로 유효하고, 임시파일이 남지 않는지
    확인한다(원자성 자체는 OS 레벨 경쟁조건이라 단위테스트로 직접
    재현하지 않는다 — os.replace의 원자성은 표준 라이브러리가 보장)."""
    heartbeat = tmp_path / "hb"

    write_heartbeat(heartbeat)

    assert heartbeat.exists()
    assert not (tmp_path / "hb.tmp").exists()
    float(heartbeat.read_text(encoding="utf-8"))  # 유효한 timestamp여야 함(예외 없이 파싱)


async def test_compute_system_equity_sums_running_allocated_capital_and_realized_pnl(pool):
    """사용자 승인(2026-09-02) FROZEN 존 수정 회귀 테스트 — 이전엔 항상
    0을 반환하던 compute_equity가 이제 RUNNING 실행들의 (allocated_capital
    + realized_pnl 합)을 실제로 집계하는지 확인. 한 실행에 종가 포지션을
    2개 심어(LEFT JOIN + GROUP BY 없이 잘못 짜면 allocated_capital이
    포지션 행 수만큼 중복 합산된다) 정확히 1번만 더해지는지도 함께 검증."""
    # 공유 dev/test DB에 다른 테스트가 남긴 RUNNING 실행이 있을 수 있어
    # (compute_system_equity는 설계상 시스템 전체를 집계) 절대값이 아니라
    # 이 테스트가 만든 데이터로 인한 증가분(delta)만 검증한다.
    baseline = await compute_system_equity(pool)

    user_id = await create_test_user(pool)
    execution_with_pnl = await _create_running_execution(pool, user_id)
    await _create_running_execution(pool, user_id)
    paused_execution = await _create_running_execution(pool, user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED' WHERE id = $1", paused_execution
        )
        for realized_pnl in (Decimal("10"), Decimal("5")):
            await conn.execute(
                """
                INSERT INTO positions (
                    user_id, symbol, exchange, strategy_id, execution_id,
                    quantity, average_entry_price, realized_pnl, entry_time, closed_at
                ) VALUES ($1, 'BTC/USDT', 'bitget', 'strat-1', $2, 0, 50000, $3, now(), now())
                """,
                user_id,
                execution_with_pnl,
                realized_pnl,
            )

    equity = await compute_system_equity(pool)

    # 두 RUNNING 실행의 allocated_capital(100+100) + realized_pnl 합(10+5).
    # PAUSED 실행의 allocated_capital(100)은 제외돼야 한다.
    assert equity - baseline == Decimal("215")


async def test_compute_system_equity_excludes_retired_and_pending_approval_executions(pool):
    """불변식 — compute_system_equity는 status='RUNNING'인 실행만 집계해야 한다.
    RETIRED/PENDING_APPROVAL 실행의 allocated_capital이 섞여 들어가면 실제보다 큰
    계좌 규모를 기준으로 손실률(peak-to-current)을 계산해 손실을 과소평가하고
    HALT/LIQUIDATE 판정을 놓칠 수 있다(FD-9.1)."""
    baseline = await compute_system_equity(pool)

    user_id = await create_test_user(pool)
    retired_execution = await _create_running_execution(pool, user_id)
    pending_execution = await _create_running_execution(pool, user_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'RETIRED' WHERE id = $1", retired_execution
        )
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PENDING_APPROVAL' WHERE id = $1",
            pending_execution,
        )

    equity = await compute_system_equity(pool)

    assert equity == baseline


async def test_run_one_cycle_leaves_execution_running_when_healthy_and_within_thresholds(
    pool, kill_switch, tmp_path
):
    """불변식 — 손실률·응답성·거래소 헬스 모두 정상이면 run_one_cycle은 RUNNING
    실행에 어떤 조치도 적용하면 안 된다(신호 없이 PAUSE되는 거짓양성 방지,
    FD-9.2)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)

    async def compute_equity() -> Decimal:
        return Decimal("10000")

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    deps = _make_cycle_deps(heartbeat, compute_equity)
    last_action = _LastAppliedAction()

    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "RUNNING"
    assert row["paused_by"] is None


async def test_run_one_cycle_halt_creates_no_liquidation_request(pool, kill_switch, tmp_path):
    """불변식 — HALT(응답불능, 손실 없음)는 신규진입 차단일 뿐 강제청산이 아니다.
    apply_decision이 action 분기를 잘못 짜면 HALT에도 liquidation_request가
    생겨 하류 슬라이서에 존재해선 안 될 청산 요청이 노출된다(watchdog_apply.py
    DoD(f) — LIQUIDATE만 liquidation_request를 만든다)."""
    user_id = await create_test_user(pool)
    await _create_running_execution(pool, user_id)
    heartbeat = tmp_path / "hb"
    heartbeat.write_text(str(time.time() - 60))  # 응답불능 시뮬레이션, 손실은 0

    async def compute_equity() -> Decimal:
        return Decimal("10000")

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    deps = _make_cycle_deps(heartbeat, compute_equity)
    last_action = _LastAppliedAction()

    async with pool.acquire() as conn:
        baseline_count = await conn.fetchval("SELECT count(*) FROM liquidation_request")

    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db)

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM liquidation_request")
    assert count == baseline_count


async def test_run_one_cycle_survives_check_db_raising_exception(pool, kill_switch, tmp_path):
    """실패주입 — check_db 콜백이 (커넥션 풀 고갈 등으로) 예외를 던지면
    Split-Brain._safe_check이 이를 삼켜 False로 처리해야 한다. run_one_cycle이
    이 경로에서 예외를 그대로 전파시켜 죽으면 5초 폴링 루프 전체가 재시도
    기회 없이 프로세스째로 죽는다 — 예외가 전파되지 않고, 단발성 실패만으로는
    (히스테리시스 3초 미만) 아직 조치가 발동하지 않는지 함께 확인한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)

    async def compute_equity() -> Decimal:
        return Decimal("10000")

    async def check_exchange() -> bool:
        return True

    async def check_db_raises() -> bool:
        raise ConnectionError("simulated pool exhaustion")

    deps = _make_cycle_deps(heartbeat, compute_equity)
    last_action = _LastAppliedAction()

    await _run_cycle(pool, deps, kill_switch, last_action, check_exchange, check_db_raises)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "RUNNING"
    assert row["paused_by"] is None
