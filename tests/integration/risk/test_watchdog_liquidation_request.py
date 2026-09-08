"""R-51 통합테스트 — liquidation_request/slice DDL + watchdog LIQUIDATE 경로.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-51 (task-2357), §5 표 156행
(DDL), §4 liquidation_request 상태표 426~432행, §9 R-51 DoD."""
from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import SplitBrainDiagnostics
from src.core.safety.watchdog import WatchdogAction, WatchdogDecision, WatchdogService
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.watchdog_process import (
    WATCHDOG_SYSTEM_ACTOR_ID,
    _apply_decision,
    _LastAppliedAction,
    _LatestExchangeHealth,
    build_kill_switch_service,
    run_one_cycle,
)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def kill_switch(pool):
    return build_kill_switch_service(pool)


@pytest.fixture(autouse=True)
async def _deactivate_watchdog_controls_after(pool):
    """이 파일의 모든 테스트가 WATCHDOG_SYSTEM_ACTOR_ID로 GLOBAL safety_control을
    만든다 — ACTIVE로 남으면 공유 테스트 DB의 다른 테스트(evaluate_pre_submit 등,
    GLOBAL 범위는 모든 tenant에 매치)가 전부 RISK_KILL_SWITCH_ACTIVE_GLOBAL로
    오염된다(test_kill_switch_service.py의 GLOBAL 테스트와 같은 이유로 같은
    finally 정리 관행을 자동화했다). deactivate()는 evidence_ref 등 커맨드
    계층을 거치지 않고 최소한으로 INACTIVE 처리만 한다 — 이 파일이 만든 통제만
    지운다(actor_subject_id로 한정)."""
    yield
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE safety_control SET state = 'INACTIVE', deactivated_at = now() "
            "WHERE actor_subject_id = $1 AND state = 'ACTIVE'",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )


async def _create_control(pool: asyncpg.Pool) -> UUID:
    """CHECK/UNIQUE/FK 거부 테스트용 safety_control 하나만 만든다 — fan-out
    (legacy pause/paper_control)이 필요 없으므로 KillSwitchService가 아니라
    그 아래 activate_safety_control()을 직접 써서(I3가 허용하는 유일한 INSERT
    경로 그대로) 공유 테스트 DB의 다른 실행을 건드리지 않는다."""
    repo = PostgresRiskGateRepository(pool)
    view = await activate_safety_control(
        repo,
        tenant_id=WATCHDOG_SYSTEM_ACTOR_ID,
        actor_subject_id=WATCHDOG_SYSTEM_ACTOR_ID,
        actor_is_admin=True,
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason="test setup",
    )
    return view.id


async def _counts(pool: asyncpg.Pool) -> tuple[int, int]:
    """WATCHDOG_SYSTEM_ACTOR_ID가 만든 control/liquidation_request 수만 센다
    — 공유 테스트 DB에 다른 테스트가 남긴 행이 섞여도 델타 비교가 안전하다."""
    async with pool.acquire() as conn:
        controls = await conn.fetchval(
            "SELECT COUNT(*) FROM safety_control WHERE actor_subject_id = $1",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
        requests = await conn.fetchval(
            "SELECT COUNT(*) FROM liquidation_request lr "
            "JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    return controls, requests


# ---- DoD(b) — 거부 입력 실증 3건 --------------------------------------------


async def test_state_check_rejects_unknown_value(pool):
    control_id = await _create_control(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO liquidation_request "
                "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
                "VALUES ($1, 'GLOBAL', '', 'FOO', 'test', 1)",
                control_id,
            )


async def test_slice_unique_request_seq_rejects_duplicate(pool):
    control_id = await _create_control(pool)
    async with pool.acquire() as conn:
        request_row = await conn.fetchrow(
            "INSERT INTO liquidation_request "
            "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
            "VALUES ($1, 'GLOBAL', '', 'REQUESTED', 'test', 1) RETURNING id",
            control_id,
        )
        request_id = request_row["id"]
        await conn.execute(
            "INSERT INTO liquidation_slice (request_id, seq, symbol, quantity, state) "
            "VALUES ($1, 1, 'BTC/USDT', 1.0, 'PENDING')",
            request_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO liquidation_slice (request_id, seq, symbol, quantity, state) "
                "VALUES ($1, 1, 'BTC/USDT', 2.0, 'PENDING')",
                request_id,
            )


async def test_safety_control_fk_rejects_unknown_id(pool):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO liquidation_request "
                "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
                "VALUES ($1, 'GLOBAL', '', 'REQUESTED', 'test', 1)",
                uuid4(),
            )


# ---- DoD(c) — LIQUIDATE는 control+request, HALT는 control만 ----------------


async def test_liquidate_creates_control_and_liquidation_request_with_fence(pool, kill_switch):
    before_controls, before_requests = await _counts(pool)

    await _apply_decision(
        pool,
        WatchdogDecision(action=WatchdogAction.LIQUIDATE, reason="market_wide_correlated_loss"),
        kill_switch,
    )

    after_controls, after_requests = await _counts(pool)
    assert after_controls - before_controls == 1
    assert after_requests - before_requests == 1

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lr.state, lr.fence_token, sc.fence_token AS control_fence "
            "FROM liquidation_request lr JOIN safety_control sc ON sc.id = lr.safety_control_id "
            "WHERE sc.actor_subject_id = $1 ORDER BY lr.requested_at DESC LIMIT 1",
            WATCHDOG_SYSTEM_ACTOR_ID,
        )
    assert row["state"] == "REQUESTED"
    assert row["fence_token"] == row["control_fence"]


async def test_halt_creates_control_only_no_liquidation_request(pool, kill_switch):
    before_controls, before_requests = await _counts(pool)

    await _apply_decision(
        pool,
        WatchdogDecision(action=WatchdogAction.HALT, reason="main_process_unresponsive"),
        kill_switch,
    )

    after_controls, after_requests = await _counts(pool)
    assert after_controls - before_controls == 1
    assert after_requests - before_requests == 0


# ---- DoD(d) — 무조건 UPDATE 제거 + 반복 사이클 rowcount 0 -------------------


def test_watchdog_process_has_no_unconditional_update_strategy_executions():
    """`UPDATE strategy_executions`는 watchdog_process.py에 전혀 없어야 한다
    — 그 전이는 이제 KillSwitchService.activate로 위임됐다(모듈 docstring)."""
    text = Path("src/watchdog_process.py").read_text(encoding="utf-8")
    assert "UPDATE strategy_executions" not in text


async def test_run_one_cycle_does_not_reapply_same_decision_twice(pool, kill_switch, tmp_path):
    """같은 판정이 연속 사이클(5초 간격)에서도 새 control을 만들지 않는다 —
    두 번째 호출은 실질적으로 아무 것도 추가하지 않는다(rowcount 0과 동치)."""
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    heartbeat.write_text(str(time.time() - 60))  # 응답불능 시뮬레이션 → HALT

    async def compute_equity() -> Decimal:
        return Decimal("0")

    async def check_exchange() -> bool:
        return True

    async def check_db() -> bool:
        return True

    exchange_health_cache = _LatestExchangeHealth()
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=heartbeat,
    )
    split_brain = SplitBrainDiagnostics()
    last_action = _LastAppliedAction()

    before_controls, _ = await _counts(pool)
    cycle_kwargs = {
        "check_exchange": check_exchange,
        "check_db": check_db,
        "exchange_health_cache": exchange_health_cache,
        "kill_switch": kill_switch,
        "last_action": last_action,
    }

    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)
    mid_controls, _ = await _counts(pool)
    assert mid_controls - before_controls == 1

    await run_one_cycle(pool, service, split_brain, **cycle_kwargs)
    after_controls, _ = await _counts(pool)
    assert after_controls - mid_controls == 0
