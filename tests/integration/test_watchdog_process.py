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
from src.watchdog_process import _apply_decision, run_one_cycle
from tests.integration.conftest import create_test_user


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


async def test_apply_decision_pauses_running_executions(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    await _apply_decision(
        pool, WatchdogDecision(action=WatchdogAction.HALT, reason="main_process_unresponsive")
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_apply_decision_records_audit_log(pool):
    user_id = await create_test_user(pool)
    await _create_running_execution(pool, user_id)

    await _apply_decision(
        pool,
        WatchdogDecision(action=WatchdogAction.LIQUIDATE, reason="market_wide_correlated_loss"),
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


async def test_stale_heartbeat_leads_to_halt_and_real_pause(pool, tmp_path):
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

    await _apply_decision(pool, decision)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_run_one_cycle_suppresses_action_on_db_isolated_failure(pool, tmp_path):
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

    service = WatchdogService(
        compute_equity=compute_equity, health_check=check_exchange, heartbeat_path=heartbeat
    )
    # 실제 5분 대신 즉시 히스테리시스가 확정되도록 임계값을 짧게.
    split_brain = SplitBrainDiagnostics(entry_confirm_seconds=0.01, recovery_confirm_seconds=0.01)

    await run_one_cycle(
        pool, service, split_brain, check_exchange=check_exchange, check_db=check_db
    )
    await asyncio.sleep(0.02)  # entry_confirm_seconds 경과시켜 히스테리시스 확정
    await run_one_cycle(
        pool, service, split_brain, check_exchange=check_exchange, check_db=check_db
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "RUNNING"
    assert row["paused_by"] is None


async def test_run_one_cycle_applies_action_when_not_db_isolated(pool, tmp_path):
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

    service = WatchdogService(
        compute_equity=compute_equity, health_check=check_exchange, heartbeat_path=heartbeat
    )
    split_brain = SplitBrainDiagnostics(entry_confirm_seconds=0.01, recovery_confirm_seconds=0.01)

    await run_one_cycle(
        pool, service, split_brain, check_exchange=check_exchange, check_db=check_db
    )
    await asyncio.sleep(0.02)
    await run_one_cycle(
        pool, service, split_brain, check_exchange=check_exchange, check_db=check_db
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"
