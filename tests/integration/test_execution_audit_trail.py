"""task-10779 (AUDIT-6 §5 G5-1/P0-3) — Execution 생성/LIVE 전환/상태 변경 진입점의
감사 로그(`record_audit_log`) D3 증빙.

진입점 전수 표 (execution_service.py + execution_control.py, 자금·모드에 영향을 주는
쓰기 전부):

| 진입점 | 파일:함수 | 감사기록(수정 전) | 감사기록(수정 후) |
|---|---|---|---|
| 실행 생성(PAPER/LIVE) | execution_service.py::create_execution | ✗ | ✓ execution.created |
| PAPER→LIVE 전환 | execution_service.py::convert_to_live | ✗ | ✓ execution.converted_to_live |
|  | (+ create_execution 재사용분 execution.created도 함께 남음) | | |
| 실행 시작 | execution_control.py::start | ✗ | ✓ execution.started |
| 실행 일시정지 | execution_control.py::pause | ✗ | ✓ execution.paused |
| 손실한도 설정 | execution_control.py::set_max_drawdown | ✗ | ✓ execution.max_drawdown_set |
| 실행 중지(retire) | execution_control.py::retire | ✗ | ✓ execution.retired |

같은 서비스 계층에 이 외의 자금/모드 영향 쓰기 진입점은 없다(모듈 전체 grep 결과,
INSERT/UPDATE strategy_executions는 위 6개 함수가 전부).

INVARIANTS.md 대조: 기존 I-01~I-12 중 이 공백을 직접 커버하는 항목은 없음(2026-10-01
미채번 공백 기록, docs/design/INVARIANTS.md 27-33행) — 이 리프가 그 공백을 코드로 닫는다.

DB 트리거 확인: `strategy_executions` 관련 마이그레이션(f2a3b4c5d6e7, 9e2b9f93ce15,
7a6b8e4ef2f5 등) 전수 검색 결과 `CREATE TRIGGER`가 없다 — 서비스 계층 외 다른 경로로
이미 기록되는 audit 경로는 존재하지 않는다(즉 (1) 선확인 결과: 없음, 직접 추가 필요).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services import execution_control, execution_service
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
    )


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _link_credential(pool, user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            user_id,
            exchange,
            b"super-secret-api-key",
        )


async def _create_execution(service, pool, user_id, *, mode="PAPER"):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    return await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode=mode,
        available_balance=Decimal("10000"),
    )


async def _audit_rows(pool, *, action_type: str, target_id: str):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, actor_agent, action_type, target_type, target_id, "
            "decision_data FROM audit_log WHERE action_type = $1 AND target_id = $2 "
            "ORDER BY log_id",
            action_type,
            target_id,
        )


# ---------------------------------------------------------------------------
# 감사 행 존재 테스트 (생성/전환/상태 변경 진입점 6개 전수)
# ---------------------------------------------------------------------------


async def test_create_paper_execution_writes_audit_row(service, pool):
    """D3 red-gate 재현 — 수정 전 코드에서는 이 단언이 0행으로 실패했다
    (AUDIT-6 §0 P0-3 재현). 수정 후에는 생성과 같은 트랜잭션에 기록된 1행을 찾는다."""
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    rows = await _audit_rows(pool, action_type="execution.created", target_id=str(created.id))
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == user_id
    assert row["target_type"] == "strategy_execution"
    data = json.loads(row["decision_data"])
    assert data["mode"] == "PAPER"
    assert data["allocated_capital"] == "500"


async def test_create_live_execution_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="LIVE")

    rows = await _audit_rows(pool, action_type="execution.created", target_id=str(created.id))
    assert len(rows) == 1
    assert json.loads(rows[0]["decision_data"])["mode"] == "LIVE"


async def test_convert_to_live_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    paper = await _create_execution(service, pool, user_id, mode="PAPER")
    await service.start(paper.id, user_id)

    live = await service.convert_to_live(
        user_id,
        paper.id,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        available_balance=Decimal("10000"),
    )

    conversion_rows = await _audit_rows(
        pool, action_type="execution.converted_to_live", target_id=str(live.id)
    )
    assert len(conversion_rows) == 1
    data = json.loads(conversion_rows[0]["decision_data"])
    assert data["source_execution_id"] == paper.id
    assert data["mode_before"] == "PAPER"
    assert data["mode_after"] == "LIVE"

    # convert_to_live()가 재사용하는 create_execution()도 자기 몫의 감사 행을 남긴다.
    creation_rows = await _audit_rows(pool, action_type="execution.created", target_id=str(live.id))
    assert len(creation_rows) == 1


async def test_start_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    await service.start(created.id, user_id)

    rows = await _audit_rows(pool, action_type="execution.started", target_id=str(created.id))
    assert len(rows) == 1
    data = json.loads(rows[0]["decision_data"])
    assert data["status_before"] == "PENDING_APPROVAL"
    assert data["status_after"] == "RUNNING"


async def test_pause_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")
    await service.start(created.id, user_id)

    await service.pause(created.id, paused_by="USER", user_id=user_id)

    rows = await _audit_rows(pool, action_type="execution.paused", target_id=str(created.id))
    assert len(rows) == 1
    assert json.loads(rows[0]["decision_data"])["paused_by"] == "USER"


async def test_set_max_drawdown_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    await service.set_max_drawdown(created.id, user_id, Decimal("10"))

    rows = await _audit_rows(
        pool, action_type="execution.max_drawdown_set", target_id=str(created.id)
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["decision_data"])["max_drawdown_pct"] == "10"


async def test_retire_writes_audit_row(service, pool):
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")
    await service.start(created.id, user_id)

    await service.retire(created.id, user_id, liquidation="KEEP_POSITIONS")

    rows = await _audit_rows(pool, action_type="execution.retired", target_id=str(created.id))
    assert len(rows) == 1
    data = json.loads(rows[0]["decision_data"])
    assert data["liquidation"] == "KEEP_POSITIONS"
    assert data["status_after"] == "RETIRED"


async def test_audit_decision_data_never_contains_credentials(service, pool):
    """부정 케이스 1/3 — 감사 기록에 자격증명/비밀값이 새지 않는지 확인
    (CLAUDE.md §3 "비밀값·자격증명 기록 금지")."""
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    rows = await _audit_rows(pool, action_type="execution.created", target_id=str(created.id))
    serialized = rows[0]["decision_data"]
    assert "super-secret-api-key" not in serialized
    assert "api_key" not in serialized
    assert "api_secret" not in serialized


# ---------------------------------------------------------------------------
# 감사 기록 실패 주입 — fail-closed(같은 트랜잭션 롤백) 검증
# ---------------------------------------------------------------------------


async def test_audit_log_failure_rolls_back_execution_creation(service, pool, monkeypatch):
    """부정 케이스 2/3·실패 주입 — record_audit_log()가 예외를 던지면
    strategy_executions INSERT도 같은 트랜잭션에서 롤백돼야 한다(fail-closed,
    자금 배정 행위가 감사 없이 남으면 안 된다는 요구의 핵심 증빙)."""
    user_id = await create_test_tenant(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    async def boom(*args, **kwargs):
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr(execution_service, "record_audit_log", boom)

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        await service.create_execution(
            user_id,
            strategy_id,
            version,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            mode="PAPER",
            available_balance=Decimal("10000"),
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM strategy_executions WHERE strategy_id = $1", strategy_id
        )
    assert count == 0


async def test_audit_log_failure_rolls_back_start_transition(service, pool, monkeypatch):
    """부정 케이스 3/3·실패 주입 — execution_control.start()의 감사 기록 실패도
    RUNNING 전이를 롤백해야 한다(상태 변경 진입점도 생성과 동일한 fail-closed 보장)."""
    user_id = await create_test_tenant(pool)
    created = await _create_execution(service, pool, user_id, mode="PAPER")

    async def boom(*args, **kwargs):
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr(execution_control, "record_audit_log", boom)

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        await service.start(created.id, user_id)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_executions WHERE id = $1", created.id
        )
    assert status == "PENDING_APPROVAL"  # RUNNING으로 전이되지 않고 롤백됨


@pytest.mark.perf
async def test_create_execution_audit_write_within_db_round_trip_budget(service, pool, perf_budget):
    """D3 성능 단언 — 감사 기록 INSERT가 추가되며 create_execution()의 DB 왕복이
    하나 늘었다(전략/자격증명 조회 + INSERT + audit INSERT). 절대 ms 임계는
    실행환경마다 흔들리므로(task-6774 관례, test_execution_control.py의 기존
    perf 테스트와 동일 패턴) 이 환경에서 직접 잰 단순 SELECT 1 왕복시간의
    1000배를 예산으로 쓴다."""
    user_id = await create_test_tenant(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    async def _baseline_round_trip() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    samples_ms = []
    for _ in range(23):
        samples_ms.append((await perf_budget.sample_async(_baseline_round_trip)).wall_ms)
    baseline_rt_ms = sorted(samples_ms[3:])[-1]

    sample = await perf_budget.sample_async(
        lambda: service.create_execution(
            user_id,
            strategy_id,
            version,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            mode="PAPER",
            available_balance=Decimal("10000"),
        )
    )

    budget_ms = max(1000.0, 1000 * baseline_rt_ms)
    assert sample.wall_ms < budget_ms, (
        f"create_execution() took {sample.wall_ms:.1f}ms, budget {budget_ms:.1f}ms "
        f"(baseline rt {baseline_rt_ms:.1f}ms)"
    )
