"""RiskGuardService 통합테스트 — 실제 TEST_DATABASE_URL 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 135행(R-41), §9 R-41 DoD —
pause 직접 호출 0건(grep), KillSwitchService.activate 정확히 1회 호출+반환
SafetyControlView 전파, 중복 트리거 멱등, 타 테넌트·타 실행 무영향."""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyControlState, SafetyScope
from src.services.risk_guard_service import RiskGuardService
from src.services.safety.kill_switch_service import KillSwitchService
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def kill_switch(pool):
    return KillSwitchService(
        risk_gate_repo=PostgresRiskGateRepository(pool),
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        audit_repo=PostgresAuditEventRepository(pool),
    )


@pytest.fixture
def risk_guard(pool, kill_switch):
    return RiskGuardService(pool, kill_switch)


async def _seed_execution(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    max_drawdown_pct: Decimal | None,
    allocated: Decimal = Decimal("1000"),
) -> tuple[int, str]:
    strategy_id = f"rg-exec-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status, max_drawdown_pct)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING', $4)
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated,
            max_drawdown_pct,
        )
    return row["id"], strategy_id


async def _insert_position(
    pool: asyncpg.Pool,
    user_id: UUID,
    execution_id: int,
    strategy_id: str,
    *,
    realized: Decimal = Decimal("0"),
    unrealized: Decimal = Decimal("0"),
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget', $2, $3, 1.0, 50000, $4, $5, now())
            """,
            user_id,
            strategy_id,
            execution_id,
            unrealized,
            realized,
        )


async def _execution_status(pool: asyncpg.Pool, execution_id: int) -> tuple[str, str | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row is not None
    return row["status"], row["paused_by"]


async def _active_controls(pool: asyncpg.Pool, scope_ref: str) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM safety_control WHERE scope = 'STRATEGY_DEPLOYMENT' "
            "AND scope_ref = $1 AND state = 'ACTIVE'",
            scope_ref,
        )
    return list(rows)


async def test_evaluate_activates_kill_switch_and_propagates_returned_view(pool, kill_switch):
    published: list[tuple[str, dict[str, object]]] = []

    async def _publish(topic: str, payload: dict[str, object]) -> None:
        published.append((topic, payload))

    risk_guard = RiskGuardService(pool, kill_switch, publish=_publish)
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(
        pool, user_id, max_drawdown_pct=Decimal("10")
    )
    # 1000 배분에 -150 손실 = -15% > 10% 한도
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-150"))

    triggered = await risk_guard.evaluate_all_running()

    assert triggered == [execution_id]
    scope_ref = f"exec:{execution_id}"
    controls = await _active_controls(pool, scope_ref)
    assert len(controls) == 1
    assert controls[0]["reason"] == "MAX_DRAWDOWN_EXCEEDED"

    status, paused_by = await _execution_status(pool, execution_id)
    assert status == "PAUSED"
    assert paused_by == "SAFETY_LAYER"

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "execution.safety_block.applied"
    assert payload["execution_id"] == execution_id
    assert payload["safety_control_id"] == str(controls[0]["id"])


async def test_evaluate_calls_kill_switch_activate_exactly_once(pool, kill_switch):
    calls: list[tuple[dict[str, object], object]] = []
    original_activate = kill_switch.activate

    async def _spy_activate(**kwargs: object):
        result = await original_activate(**kwargs)
        calls.append((kwargs, result))
        return result

    kill_switch.activate = _spy_activate  # type: ignore[method-assign]
    risk_guard = RiskGuardService(pool, kill_switch)
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(
        pool, user_id, max_drawdown_pct=Decimal("10")
    )
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-150"))

    await risk_guard.evaluate_all_running()

    assert len(calls) == 1
    kwargs, view = calls[0]
    assert kwargs["scope"] == SafetyScope.STRATEGY_DEPLOYMENT
    assert kwargs["scope_ref"] == f"exec:{execution_id}"
    assert view.scope_ref == f"exec:{execution_id}"
    assert view.state == SafetyControlState.ACTIVE


async def test_duplicate_trigger_does_not_create_second_control(pool, risk_guard):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(
        pool, user_id, max_drawdown_pct=Decimal("10")
    )
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-150"))

    first = await risk_guard.evaluate_all_running()
    assert first == [execution_id]
    scope_ref = f"exec:{execution_id}"
    controls_after_first = await _active_controls(pool, scope_ref)
    assert len(controls_after_first) == 1
    fence_after_first = controls_after_first[0]["fence_token"]

    # fan-out이 아직 상태를 못 바꾼 레이스를 흉내낸다 — 그래도 이미 ACTIVE인
    # control이 있으면 재호출하지 않아야 한다(멱등).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'RUNNING' WHERE id = $1", execution_id
        )

    second = await risk_guard.evaluate_all_running()

    assert second == []
    controls_after_second = await _active_controls(pool, scope_ref)
    assert len(controls_after_second) == 1
    assert controls_after_second[0]["fence_token"] == fence_after_first


async def test_evaluate_does_not_affect_other_users_execution(pool, risk_guard):
    breaching_user = await create_test_user(pool)
    safe_user = await create_test_user(pool)
    breaching_id, breaching_strategy = await _seed_execution(
        pool, breaching_user, max_drawdown_pct=Decimal("10")
    )
    safe_id, safe_strategy = await _seed_execution(pool, safe_user, max_drawdown_pct=Decimal("10"))
    await _insert_position(
        pool, breaching_user, breaching_id, breaching_strategy, realized=Decimal("-150")
    )
    await _insert_position(pool, safe_user, safe_id, safe_strategy, realized=Decimal("-10"))

    triggered = await risk_guard.evaluate_all_running()

    assert triggered == [breaching_id]
    safe_status, _ = await _execution_status(pool, safe_id)
    assert safe_status == "RUNNING"
    assert await _active_controls(pool, f"exec:{safe_id}") == []


async def test_evaluate_leaves_execution_within_limit_running(pool, risk_guard):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(
        pool, user_id, max_drawdown_pct=Decimal("10")
    )
    # 1000 배분에 -50 손실 = -5% < 10% 한도
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-50"))

    triggered = await risk_guard.evaluate_all_running()

    assert triggered == []
    status, _ = await _execution_status(pool, execution_id)
    assert status == "RUNNING"
    assert await _active_controls(pool, f"exec:{execution_id}") == []


async def test_evaluate_ignores_execution_without_guard_set(pool, risk_guard):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(pool, user_id, max_drawdown_pct=None)
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-900"))

    triggered = await risk_guard.evaluate_all_running()

    assert triggered == []


async def test_main_query_db_failure_propagates_not_swallowed(pool, risk_guard, monkeypatch):
    """실패주입(D2, 진짜 DB 오류) — 실행·포지션 집계 쿼리가 DB 레벨에서
    실패하면 예외가 그대로 전파돼야 한다. 조용히 삼키고 빈 목록을 반환하면
    호출자(백그라운드 루프)가 "정지 대상 없음"으로 착각해 실제 한도 초과
    실행을 방치하게 된다(fail-closed, I2)."""
    original_fetch = asyncpg.Connection.fetch

    async def failing_fetch(self, query, *args, **kwargs):
        if "strategy_executions" in query and "positions" in query:
            raise asyncpg.PostgresConnectionError("simulated evaluate_all_running failure")
        return await original_fetch(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", failing_fetch)

    with pytest.raises(asyncpg.PostgresConnectionError):
        await risk_guard.evaluate_all_running()


async def test_evaluate_all_running_stays_fast_at_scale(pool, risk_guard):
    """성능 단언(D2) — 실행-포지션 조인·집계가 200개 규모에서도 인덱스
    기반으로 짧게 끝나는지 확인한다(순차 스캔이면 시간이 행 수에 비례해
    늘어난다). 전부 한도 미달로 시딩해 activate() 오버헤드 없이 쿼리
    자체의 비용만 측정한다(웜업 1회로 최초 쿼리플랜 컴파일 비용 제외)."""
    user_id = await create_test_user(pool)
    for _ in range(200):
        execution_id, strategy_id = await _seed_execution(
            pool, user_id, max_drawdown_pct=Decimal("50")
        )
        await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-1"))

    await risk_guard.evaluate_all_running()  # 웜업

    start = time.perf_counter()
    triggered = await risk_guard.evaluate_all_running()
    elapsed = time.perf_counter() - start

    assert triggered == []
    assert elapsed < 2.0, f"200개 규모 evaluate_all_running이 {elapsed:.3f}s 걸렸다"


async def test_two_instances_concurrent_evaluate_creates_exactly_one_control(pool):
    """다중 인스턴스 동시성(D3) — 서로 다른 프로세스를 흉내내는 두 개의
    독립 RiskGuardService(각자 독립 KillSwitchService)가 같은 한도 초과
    실행을 동시에 평가해도 activate()가 정확히 한 번만 성공하고, ACTIVE
    control이 정확히 하나만 남아야 한다(TOCTOU 레이스로 인한 이중 정지
    방지 — 멱등은 이 계층 책임이라는 모듈 독스트링의 주장을 실제 동시
    실행으로 증명한다)."""
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _seed_execution(
        pool, user_id, max_drawdown_pct=Decimal("10")
    )
    await _insert_position(pool, user_id, execution_id, strategy_id, realized=Decimal("-150"))

    def _make_guard() -> RiskGuardService:
        kill_switch = KillSwitchService(
            risk_gate_repo=PostgresRiskGateRepository(pool),
            pg_pool=pool,
            paper_control_repo=PostgresPaperControlRepository(pool),
            exchange_adapters={"bitget": FakeExchangeAdapter(exchange_name="bitget")},
            audit_repo=PostgresAuditEventRepository(pool),
        )
        return RiskGuardService(pool, kill_switch)

    guard_a = _make_guard()
    guard_b = _make_guard()

    results = await asyncio.gather(guard_a.evaluate_all_running(), guard_b.evaluate_all_running())

    triggered_ids = [eid for result in results for eid in result]
    assert triggered_ids == [execution_id], "정확히 한 인스턴스만 트리거해야 한다"

    controls = await _active_controls(pool, f"exec:{execution_id}")
    assert len(controls) == 1, f"ACTIVE control이 {len(controls)}개 생성됐다(레이스로 이중 생성)"


def test_kill_switch_is_a_required_constructor_dependency() -> None:
    """DoD(5) — 안전 게이트 파라미터에 Optional/None 기본값 금지(EO-06/I-01)."""
    sig = inspect.signature(RiskGuardService.__init__)
    param = sig.parameters["kill_switch"]
    assert param.default is inspect.Parameter.empty
    assert param.annotation in ("KillSwitchService", KillSwitchService)


def test_risk_guard_service_has_no_direct_execution_stop_paths() -> None:
    """DoD(1) — 레거시 pause 호출·INSERT INTO safety_control·executions
    status 직접 UPDATE 0건을 grep으로 강제한다."""
    src_path = (
        Path(__file__).resolve().parents[3] / "src" / "services" / "risk_guard_service.py"
    )
    text = src_path.read_text(encoding="utf-8")
    assert not re.search(r"\.pause\(", text)
    assert not re.search(r"INSERT\s+INTO\s+safety_control", text, re.IGNORECASE)
    assert not re.search(r"UPDATE\s+strategy_executions", text, re.IGNORECASE)


def test_risk_guard_service_file_is_at_most_90_lines() -> None:
    src_path = (
        Path(__file__).resolve().parents[3] / "src" / "services" / "risk_guard_service.py"
    )
    line_count = len(src_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 90, f"risk_guard_service.py는 90줄 이하여야 합니다: {line_count}줄"
