"""KillSwitchService 통합테스트 — 실제 TEST_DATABASE_URL 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 표 136행(R-40), §4.3
상태표 412~413행, §9 R-40 DoD("5범위 통합, 타 테넌트 미영향,
`INSERT INTO safety_control` 호출부 1곳 grep")."""
from __future__ import annotations

import os
import re
import uuid
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService, MissingEvidenceRefError
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def risk_gate_repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def paper_control_repo(pool):
    return PostgresPaperControlRepository(pool)


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


@pytest.fixture
def service(pool, risk_gate_repo, paper_control_repo, audit_repo):
    return KillSwitchService(
        risk_gate_repo=risk_gate_repo,
        pg_pool=pool,
        paper_control_repo=paper_control_repo,
        exchange_adapters={"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        audit_repo=audit_repo,
    )


async def _seed_order(
    pool: asyncpg.Pool, user_id: UUID, *, exchange: str = "bitget", status: str = "SUBMITTED"
) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id,
                strategy_version, symbol, exchange, side, order_type, quantity, status
            ) VALUES (
                $1, $2, $3, 'kill-switch-test', '1.0.0', 'BTC/USDT', $4, 'BUY',
                'LIMIT', 1.0, $5
            )
            RETURNING order_id
            """,
            user_id,
            f"ks-{uuid.uuid4().hex}",
            f"ex-{uuid.uuid4().hex[:12]}",
            exchange,
            status,
        )
    return row["order_id"]


async def _seed_running_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str) -> int:
    strategy_id = f"ks-exec-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
        )
    return row["id"]


async def _execution_status(pool: asyncpg.Pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row is not None
    return row["status"]


async def _order_status(pool: asyncpg.Pool, order_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert row is not None
    return row["status"]


async def test_activate_returns_active_control_with_incremented_fence(pool, service):
    tenant_id = await create_test_user(pool)

    view = await service.activate(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="단일 진입점 기본 동작 테스트",
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    assert view.state.value == "ACTIVE"
    assert view.fence_token >= 1


async def test_activate_fans_out_legacy_pause_and_order_sweep_for_account_scope(pool, service):
    """§4.3 412행 — activate 한 번으로 legacy 정지 + 미체결 정리가 모두
    일어나야 한다(현재 라우터는 open_order_sweeper를 전혀 안 부른다는
    격차를 이 서비스가 메운다)."""
    tenant_id = await create_test_user(pool)
    execution_id = await _seed_running_execution(pool, tenant_id, exchange="bitget")
    order_id = await _seed_order(pool, tenant_id, exchange="bitget")

    await service.activate(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="계정 정지 fan-out 테스트",
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    assert await _execution_status(pool, execution_id) == "PAUSED"
    assert await _order_status(pool, order_id) == "CANCEL_REQUESTED"


async def test_activate_does_not_affect_other_tenants_execution_or_order(pool, service):
    """negative test — DoD "타 테넌트 미영향"."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    execution_b = await _seed_running_execution(pool, tenant_b, exchange="bitget")
    order_b = await _seed_order(pool, tenant_b, exchange="bitget")

    await service.activate(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_a),
        reason="타 테넌트 미영향 테스트",
        actor_subject_id=tenant_a,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    assert await _execution_status(pool, execution_b) == "RUNNING"
    assert await _order_status(pool, order_b) == "SUBMITTED"


async def test_activate_global_scope_pauses_across_tenants(pool, service):
    """GLOBAL 범위 activate 한 번으로 서로 다른 tenant/거래소의 실행이 모두
    PAUSED로 전이돼야 한다 — audit_event는 GLOBAL이 `tenant_id IS NULL`로
    기록돼(§79 §1) `get_audit_timeline`(tenant_id 필수 파라미터)로는 조회할
    수 없으므로, tenant 범위 audit 검증은 별도 테스트
    (`test_deactivate_marks_inactive_and_records_evidence_audit_event`)가
    맡는다."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    execution_a = await _seed_running_execution(pool, tenant_a, exchange="bitget")
    execution_b = await _seed_running_execution(pool, tenant_b, exchange="binance")
    admin_id = await create_test_user(pool)

    view = await service.activate(
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason="글로벌 정지 통합 테스트",
        actor_subject_id=admin_id,
        actor_is_admin=True,
        trace_id=uuid4(),
    )
    try:
        assert await _execution_status(pool, execution_a) == "PAUSED"
        assert await _execution_status(pool, execution_b) == "PAUSED"
    finally:
        # GLOBAL 통제를 안 끄면 이 공유 테스트 DB의 다른 테스트까지 오염된다
        # (test_risk_gate_lifecycle.py와 동일한 원칙).
        await service.deactivate(
            view.id,
            evidence_ref="teardown",
            actor_subject_id=admin_id,
            actor_is_admin=True,
            trace_id=uuid4(),
        )


async def test_deactivate_without_evidence_ref_is_rejected(pool, service):
    """negative test — §4.3 413행 guard "evidence_ref 필수"(fail-closed)."""
    tenant_id = await create_test_user(pool)
    view = await service.activate(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="evidence_ref 누락 테스트",
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    with pytest.raises(MissingEvidenceRefError):
        await service.deactivate(
            view.id,
            evidence_ref="",
            actor_subject_id=tenant_id,
            actor_is_admin=False,
            trace_id=uuid4(),
        )

    # 거부됐으니 control은 여전히 ACTIVE여야 한다.
    control = await service._risk_gate_repo.get_safety_control(view.id)
    assert control is not None
    assert control.state.value == "ACTIVE"


async def test_deactivate_marks_inactive_and_records_evidence_audit_event(pool, service):
    tenant_id = await create_test_user(pool)
    view = await service.activate(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="해제 테스트",
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    deactivated = await service.deactivate(
        view.id,
        evidence_ref="incident-report:INC-42",
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        trace_id=uuid4(),
    )

    assert deactivated.state.value == "INACTIVE"
    page = await get_audit_timeline(service._audit_repo, tenant_id=tenant_id, limit=20)
    matching = [
        e
        for e in page.items
        if e.action == "safety_control_deactivation_evidence_recorded"
        and e.aggregate_id == view.id
    ]
    assert len(matching) == 1
    assert matching[0].payload == {"evidence_ref": "incident-report:INC-42"}


def test_insert_into_safety_control_has_exactly_one_call_site():
    """I3(§8 392행) — kill switch 권위 INSERT는 `postgres_repository.py`
    한 곳뿐이어야 한다. 실제 SQL 문자열 리터럴만 잡도록 여는 따옴표가 바로
    앞에 오는 패턴만 매칭한다 — 이 파일이나 `kill_switch_service.py` 같은
    docstring 산문 속 언급은 매칭되지 않는다."""
    pattern = re.compile(r"""["']\s*INSERT\s+INTO\s+safety_control\b""", re.IGNORECASE)
    src_root = Path(__file__).resolve().parents[3] / "src"
    assert src_root.is_dir()

    hits: list[str] = []
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(src_root.parent)}:{lineno}")

    assert len(hits) == 1, f"safety_control INSERT 호출부가 정확히 1곳이어야 합니다: {hits}"
