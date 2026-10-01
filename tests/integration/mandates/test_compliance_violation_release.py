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

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.mandates.application.evaluate_post_trade import run_daily_post_trade_batch
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import SafetyControlAlreadyActiveError
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "task-10490 실결함 재현(고치지 않음, needs_decision): "
        "insert_safety_control()의 'SELECT ... FOR UPDATE'(postgres_repository.py)는 "
        "매치되는 ACTIVE 행이 '있을 때'만 그 행을 잠근다. 아직 ACTIVE 행이 없는 "
        "최초 activate 경쟁에서는 FOR UPDATE가 아무 것도 잠그지 않아(TOCTOU) 두 개 "
        "이상의 동시 트랜잭션이 '기존 ACTIVE 없음'을 함께 보고 통과, fence token을 "
        "중복 소모하고 같은 (scope, scope_ref)에 ACTIVE control을 2개 이상 만든다."
    ),
)
async def test_concurrent_activate_same_scope_only_one_winner_and_one_fence_token(
    pool: asyncpg.Pool, kill_switch: KillSwitchService
) -> None:
    """CM-12 D3 adversarial/concurrency -- N개의 동시 activate() 가 같은
    (scope, scope_ref) 를 두고 경쟁하면 `insert_safety_control()` 의
    `SELECT ... FOR UPDATE`(postgres_repository.py)가 직렬화해 정확히 1개만
    성공하고 나머지는 `SafetyControlAlreadyActiveError` 로 거부되어야 한다
    (R-40 §5 "트랜잭션 경계", 레드팀 #2026-09-02-26/task-8882/9065). 거부된
    요청이 fence token 을 소모하면 안 되므로, `safety_fence.current_token`
    은 활성화 시도 횟수와 무관하게 정확히 1 만큼만 증가해야 한다.

    실행 결과: 동시 10건 중 2건이 성공해 이 기대를 깬다 -- 최초 ACTIVE 행이
    없는 경쟁에서는 FOR UPDATE가 잠글 행이 없어 직렬화가 성립하지 않는
    실제 결함(TOCTOU)이다. 제품 코드는 고치지 않는다(task 규칙) -- 이
    xfail(strict=True)이 그 재현이다."""
    tenant_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        before_token = await conn.fetchval(
            "SELECT current_token FROM safety_fence WHERE scope = 'TENANT' AND scope_ref = $1",
            str(tenant_id),
        )
    before_token = before_token or 0

    async def _activate_one(idx: int):
        return await kill_switch.activate(
            scope=SafetyScope.TENANT,
            scope_ref=str(tenant_id),
            reason=f"COMPLIANCE:CM-12-concurrent-{idx}",
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            trace_id=uuid.uuid4(),
        )

    n = 10
    results = await asyncio.gather(*[_activate_one(i) for i in range(n)], return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, SafetyControlAlreadyActiveError)]
    other_errors = [
        r
        for r in results
        if isinstance(r, Exception) and not isinstance(r, SafetyControlAlreadyActiveError)
    ]
    assert not other_errors, f"예상치 못한 예외 타입: {other_errors!r}"
    assert len(successes) == 1, f"동시 activate {n}건 중 {len(successes)}개가 성공 -- 1개만 허용"
    assert len(conflicts) == n - 1, f"나머지 {n - 1}건은 SafetyControlAlreadyActiveError 여야 함"

    async with pool.acquire() as conn:
        after_token = await conn.fetchval(
            "SELECT current_token FROM safety_fence WHERE scope = 'TENANT' AND scope_ref = $1",
            str(tenant_id),
        )
    assert after_token == before_token + 1, (
        f"fence token 은 거부된 시도와 무관하게 1만 증가해야 함: before={before_token}, "
        f"after={after_token}"
    )


async def test_concurrent_deactivate_same_control_only_one_winner(
    pool: asyncpg.Pool, kill_switch: KillSwitchService
) -> None:
    """CM-12 D3 adversarial/concurrency -- 같은 control_id 에 대한 동시
    deactivate() 호출은 정확히 1개만 성공(`UPDATE ... WHERE state = 'ACTIVE'`
    가 행을 1번만 집어간다, postgres_repository.py `deactivate_safety_control`)
    하고 나머지는 `ConcurrencyConflictError` 로 거부되어야 한다 -- 이미
    INACTIVE 로 표시된 control 을 다시 해제 처리(예: 감사 이벤트 중복 기록)
    하지 않는다는 것의 동시성 하에서의 증빙."""
    tenant_id = await create_test_tenant(pool)
    control = await kill_switch.activate(
        scope=SafetyScope.TENANT,
        scope_ref=str(tenant_id),
        reason="COMPLIANCE:CM-12-concurrent-deactivate",
        actor_subject_id=tenant_id,
        actor_is_admin=True,
        trace_id=uuid.uuid4(),
    )

    async def _deactivate_one(idx: int):
        return await kill_switch.deactivate(
            control.id,
            evidence_ref=f"recovery-review-concurrent-{idx}",
            actor_subject_id=tenant_id,
            actor_is_admin=True,
            trace_id=uuid.uuid4(),
        )

    n = 10
    results = await asyncio.gather(*[_deactivate_one(i) for i in range(n)], return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    other_errors = [
        r
        for r in results
        if isinstance(r, Exception) and not isinstance(r, ConcurrencyConflictError)
    ]
    assert not other_errors, f"예상치 못한 예외 타입: {other_errors!r}"
    assert len(successes) == 1, f"동시 deactivate {n}건 중 {len(successes)}개가 성공 -- 1개만 허용"
    assert len(conflicts) == n - 1, f"나머지 {n - 1}건은 ConcurrencyConflictError 여야 함"

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control.id)
    assert state == "INACTIVE"
