"""L4_compliance_and_regulatory_v1.0.md#9 CM-12 -- 위반 알림 + 차단 해제 워크플로 통합테스트,
실 TEST_DATABASE_URL 대상.

task-2667 DoD mapping ("차단·해제 감사"): (a) 위반이 새로 차단되면 알림 이벤트가 정확히
그 rule마다 1건 발행된다. (b) negative -- 같은 배치를 재실행해도(이미 ACTIVE인 차단) 재알림
하지 않는다. (c) negative -- `evidence_ref` 없이는 해제 자체가 거부된다(CM-11이 만든
COMPLIANCE 차단도 R-40의 evidence_ref 게이트를 그대로 통과해야 한다는 통합 증명).
(d) 차단 -> 해제 전 구간은 사전 게이트 DENY, 해제 후에는 ALLOW로 열리고, 해제 자체가
`foundation_audit_event`에 감사 기록을 남긴다.

CM-11(`test_post_trade_batch.py`)의 위반 판정 로직(wash_trade)을 그대로 재사용한다 -- 이
파일은 CM-12의 추가분(알림·해제)만 검증하고 판정 자체를 다시 검증하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.mandates.application.evaluate_post_trade import run_daily_post_trade_batch
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from src.services.safety.kill_switch_service import KillSwitchService, MissingEvidenceRefError
from tests.integration.conftest import create_test_tenant

_EXCHANGE = "bitget"
_SYMBOL = "BTC/USDT"
_BUSINESS_DATE = date(2026, 1, 6)


def _asyncpg_dsn() -> str:
    import os

    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


@pytest.fixture
def risk_gate_repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def kill_switch(pool, risk_gate_repo, audit_repo):
    return KillSwitchService(
        risk_gate_repo=risk_gate_repo,
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={},
        audit_repo=audit_repo,
    )


async def _seed_wash_trade_violation(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    async with pool.acquire() as conn:
        open_order_id = await conn.fetchval(
            "INSERT INTO orders (user_id, client_order_id, strategy_id, strategy_version, "
            "symbol, exchange, side, order_type, quantity, price, status) VALUES "
            "($1, $2, 'cm12-test', '1.0.0', $3, $4, 'SELL', 'LIMIT', 1.0, $5, 'ACKNOWLEDGED') "
            "RETURNING order_id",
            tenant_id,
            f"cm12-open-{uuid.uuid4().hex}",
            _SYMBOL,
            _EXCHANGE,
            Decimal("100"),
        )
        assert open_order_id is not None
        fill_order_id = await conn.fetchval(
            "INSERT INTO orders (user_id, client_order_id, strategy_id, strategy_version, "
            "symbol, exchange, side, order_type, quantity, price, status) VALUES "
            "($1, $2, 'cm12-test', '1.0.0', $3, $4, 'BUY', 'LIMIT', 1.0, $5, 'FILLED') "
            "RETURNING order_id",
            tenant_id,
            f"cm12-fill-{uuid.uuid4().hex}",
            _SYMBOL,
            _EXCHANGE,
            Decimal("100"),
        )
        venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
        await conn.execute(
            "INSERT INTO fills (provider_fill_id, venue, order_id, exchange_order_id, symbol, "
            "side, quantity, price, fee, fee_currency, liquidity, venue_ts) VALUES "
            "($1, $2, $3, $4, $5, 'BUY', 5.0, $6, 0.0, 'USDT', 'TAKER', $7)",
            f"fill-{uuid.uuid4().hex}",
            _EXCHANGE,
            fill_order_id,
            f"ex-{uuid.uuid4().hex[:12]}",
            _SYMBOL,
            Decimal("100"),
            venue_ts,
        )


async def _run_batch(
    pool: asyncpg.Pool,
    kill_switch: KillSwitchService,
    risk_gate_repo,
    *,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
):
    return await run_daily_post_trade_batch(
        pool,
        kill_switch,
        risk_gate_repo,
        business_date=_BUSINESS_DATE,
        now=datetime.now(timezone.utc),
        publish=publish,
    )


async def test_violation_publishes_one_notification_per_rule(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """이 파일의 다른 테스트도(`test_post_trade_batch.py`처럼) 같은
    _BUSINESS_DATE에 tenant를 누적시키므로, 배치가 훑는 전체 이벤트가 아니라
    이 테스트가 만든 tenant_id로 필터링해서 단언한다."""
    tenant_id = await create_test_tenant(pool)
    await _seed_wash_trade_violation(pool, tenant_id)
    events: list[tuple[str, dict[str, Any]]] = []

    async def collect(event_type: str, payload: dict[str, Any]) -> None:
        events.append((event_type, payload))

    report = await _run_batch(pool, kill_switch, risk_gate_repo, publish=collect)

    assert report.blocked.get(tenant_id) == ("WASH_TRADE",)
    own_events = [e for e in events if e[1]["user_id"] == str(tenant_id)]
    assert [e[0] for e in own_events] == ["execution.safety_block.applied"]
    assert own_events[0][1]["rule_code"] == "WASH_TRADE"


async def test_rerun_does_not_renotify_already_blocked_tenant(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """negative -- CM-11's idempotent block (task-2865's advisory lock)
    must not be paired with a notification that fires again on every rerun."""
    tenant_id = await create_test_tenant(pool)
    await _seed_wash_trade_violation(pool, tenant_id)
    events: list[tuple[str, dict[str, Any]]] = []

    async def collect(event_type: str, payload: dict[str, Any]) -> None:
        events.append((event_type, payload))

    await _run_batch(pool, kill_switch, risk_gate_repo, publish=collect)
    await _run_batch(pool, kill_switch, risk_gate_repo, publish=collect)

    own_events = [e for e in events if e[1]["user_id"] == str(tenant_id)]
    assert len(own_events) == 1


async def test_release_without_evidence_ref_is_rejected(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """negative -- a COMPLIANCE-reason TENANT block goes through the same
    R-40 evidence_ref fail-closed gate as any other kill switch; the gate
    must stay DENY afterwards."""
    tenant_id = await create_test_tenant(pool)
    await _seed_wash_trade_violation(pool, tenant_id)
    await _run_batch(pool, kill_switch, risk_gate_repo, publish=None)

    controls = await risk_gate_repo.list_active_controls(tenant_id=tenant_id)
    compliance_controls = [
        c for c in controls if c.scope == SafetyScope.TENANT and c.reason.startswith("COMPLIANCE:")
    ]
    assert len(compliance_controls) == 1
    control_id = compliance_controls[0].id

    with pytest.raises(MissingEvidenceRefError):
        await kill_switch.deactivate(
            control_id,
            evidence_ref="",
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            trace_id=uuid.uuid4(),
        )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    decision = await gate(
        OrderContext(
            user_id=tenant_id, execution_id=None, exchange=_EXCHANGE, mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.DENY


async def test_release_with_evidence_ref_reopens_gate_and_is_audited(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo, audit_repo
) -> None:
    tenant_id = await create_test_tenant(pool)
    await _seed_wash_trade_violation(pool, tenant_id)
    await _run_batch(pool, kill_switch, risk_gate_repo, publish=None)

    controls = await risk_gate_repo.list_active_controls(tenant_id=tenant_id)
    compliance_controls = [
        c for c in controls if c.scope == SafetyScope.TENANT and c.reason.startswith("COMPLIANCE:")
    ]
    assert len(compliance_controls) == 1
    control_id = compliance_controls[0].id

    trace_id = uuid.uuid4()
    await kill_switch.deactivate(
        control_id,
        evidence_ref="recovery-review-cm12-1",
        actor_subject_id=tenant_id,
        actor_is_admin=True,
        trace_id=trace_id,
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    decision = await gate(
        OrderContext(
            user_id=tenant_id, execution_id=None, exchange=_EXCHANGE, mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.ALLOW

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action FROM foundation_audit_event WHERE aggregate_id = $1 "
            "AND aggregate_type = 'safety_control' ORDER BY sequence_no",
            control_id,
        )
    actions = [row["action"] for row in rows]
    assert "safety_control_deactivation_evidence_recorded" in actions
    assert "safety_control_deactivated" in actions
