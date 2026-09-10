"""L4_compliance_and_regulatory_v1.0.md#9 CM-11 -- `run_daily_post_trade_batch`
통합테스트, 실 TEST_DATABASE_URL 대상.

task-2509 DoD mapping: (b) wash_trade 위반이 판정되면 그 tenant의 다음 주문이
실제 `foundation_gate.py` 사전 게이트에서 COMPLIANCE 사유로 거부된다(실제
거부 단언). (c) 같은 배치를 두 번 돌려도 위반 레코드(ACTIVE safety_control)는
1건. (e) `background_loops.py`의 스케줄러 배선 자체는 여기서 검증하지 않고
`test_background_loops_wires_post_trade_batch`(아래)가 import 레벨로
증명한다 -- 배선을 지우면 그 테스트가 실패한다.
"""

from __future__ import annotations

import asyncio
import os
import time as time_module
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.mandates.application.evaluate_post_trade import run_daily_post_trade_batch
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from src.services.safety.kill_switch_service import KillSwitchService
from tests.integration.conftest import create_test_tenant

_EXCHANGE = "bitget"
_SYMBOL = "BTC/USDT"
_BUSINESS_DATE = date(2026, 1, 5)


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
def kill_switch(pool, risk_gate_repo):
    return KillSwitchService(
        risk_gate_repo=risk_gate_repo,
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={},
        audit_repo=PostgresAuditEventRepository(pool),
    )


async def _seed_order(
    pool: asyncpg.Pool, tenant_id: UUID, *, side: str, price: Decimal, status: str
) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO orders (user_id, client_order_id, strategy_id, strategy_version, "
            "symbol, exchange, side, order_type, quantity, price, status) VALUES "
            "($1, $2, 'cm11-test', '1.0.0', $3, $4, $5, 'LIMIT', 1.0, $6, $7) "
            "RETURNING order_id",
            tenant_id,
            f"cm11-{uuid.uuid4().hex}",
            _SYMBOL,
            _EXCHANGE,
            side,
            price,
            status,
        )
    return row["order_id"]


async def _seed_fill(
    pool: asyncpg.Pool, order_id: UUID, *, side: str, price: Decimal, venue_ts: datetime
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO fills (provider_fill_id, venue, order_id, exchange_order_id, symbol, "
            "side, quantity, price, fee, fee_currency, liquidity, venue_ts) VALUES "
            "($1, $2, $3, $4, $5, $6, 5.0, $7, 0.0, 'USDT', 'TAKER', $8)",
            f"fill-{uuid.uuid4().hex}",
            _EXCHANGE,
            order_id,
            f"ex-{uuid.uuid4().hex[:12]}",
            _SYMBOL,
            side,
            price,
            venue_ts,
        )


async def _seed_position(pool: asyncpg.Pool, tenant_id: UUID, *, quantity: Decimal) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) VALUES ($1, $2, $3, 'cm11-test', $4, 100, now())",
            tenant_id,
            _SYMBOL,
            _EXCHANGE,
            quantity,
        )


async def _active_compliance_controls(pool: asyncpg.Pool, tenant_id: UUID) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT reason FROM safety_control WHERE scope = 'TENANT' AND scope_ref = $1 "
            "AND state = 'ACTIVE' AND reason LIKE 'COMPLIANCE:%'",
            str(tenant_id),
        )
    return [row["reason"] for row in rows]


async def test_wash_trade_violation_blocks_next_order_at_pre_submit_gate(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    tenant_id = await create_test_tenant(pool)
    # 아직 살아있는 SELL 주문 -- CM-9 wash_trade의 "open_orders" 입력.
    await _seed_order(pool, tenant_id, side="SELL", price=Decimal("100"), status="ACKNOWLEDGED")
    # 그것과 크로스되는 별도 체결(BUY @100) -- 같은 tenant, 같은 심볼.
    fill_order_id = await _seed_order(
        pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED"
    )
    venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    await _seed_fill(pool, fill_order_id, side="BUY", price=Decimal("100"), venue_ts=venue_ts)

    report = await run_daily_post_trade_batch(
        pool,
        kill_switch,
        risk_gate_repo,
        business_date=_BUSINESS_DATE,
        now=datetime.now(timezone.utc),
    )
    assert report.blocked.get(tenant_id) == ("WASH_TRADE",)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    decision = await gate(
        OrderContext(
            user_id=tenant_id, execution_id=None, exchange=_EXCHANGE, mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_KILL_SWITCH_ACTIVE_TENANT",)


async def test_rerunning_same_batch_keeps_a_single_violation_record(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    tenant_id = await create_test_tenant(pool)
    await _seed_order(pool, tenant_id, side="SELL", price=Decimal("100"), status="ACKNOWLEDGED")
    fill_order_id = await _seed_order(
        pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED"
    )
    venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    await _seed_fill(pool, fill_order_id, side="BUY", price=Decimal("100"), venue_ts=venue_ts)

    now = datetime.now(timezone.utc)
    await run_daily_post_trade_batch(
        pool, kill_switch, risk_gate_repo, business_date=_BUSINESS_DATE, now=now
    )
    await run_daily_post_trade_batch(
        pool, kill_switch, risk_gate_repo, business_date=_BUSINESS_DATE, now=now
    )

    reasons = await _active_compliance_controls(pool, tenant_id)
    assert len(reasons) == 1


async def test_no_violation_leaves_gate_open(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """negative test -- 정상 체결만 있으면 아무것도 차단하지 않는다."""
    tenant_id = await create_test_tenant(pool)
    order_id = await _seed_order(pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED")
    venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    await _seed_fill(pool, order_id, side="BUY", price=Decimal("100"), venue_ts=venue_ts)

    report = await run_daily_post_trade_batch(
        pool,
        kill_switch,
        risk_gate_repo,
        business_date=_BUSINESS_DATE,
        now=datetime.now(timezone.utc),
    )
    assert tenant_id not in report.blocked

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    decision = await gate(
        OrderContext(
            user_id=tenant_id, execution_id=None, exchange=_EXCHANGE, mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.ALLOW


async def test_market_abuse_warn_only_leaves_gate_open(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """게이트/CI 적색 회귀 -- CM-10 market_abuse.wash_trade는 WARN 전용(§3
    "WARN은 통과시키되 기록")이라, 반대 방향 체결 두 건이 60초 안에 잡혀도
    실제 사전 게이트는 ALLOW(녹색)를 유지해야 한다. 포지션을 미리 채워
    (quantity=10) SELL 체결이 CM-9 short_sale(DENY)로 새지 않게 하고, 두
    체결 모두 FILLED(open_orders 없음)라 CM-9 wash_trade(DENY)도 걸리지
    않는다 -- 남는 것은 CM-10 WARN 하나뿐이어야 한다."""
    tenant_id = await create_test_tenant(pool)
    await _seed_position(pool, tenant_id, quantity=Decimal("10"))
    buy_order_id = await _seed_order(
        pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED"
    )
    sell_order_id = await _seed_order(
        pool, tenant_id, side="SELL", price=Decimal("100"), status="FILLED"
    )
    base_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    await _seed_fill(pool, buy_order_id, side="BUY", price=Decimal("100"), venue_ts=base_ts)
    await _seed_fill(
        pool,
        sell_order_id,
        side="SELL",
        price=Decimal("100"),
        venue_ts=base_ts + timedelta(seconds=5),
    )

    report = await run_daily_post_trade_batch(
        pool,
        kill_switch,
        risk_gate_repo,
        business_date=_BUSINESS_DATE,
        now=datetime.now(timezone.utc),
    )
    assert tenant_id not in report.blocked
    assert "market_abuse.wash_trade" in report.warnings.get(tenant_id, ())

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    decision = await gate(
        OrderContext(
            user_id=tenant_id, execution_id=None, exchange=_EXCHANGE, mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.ALLOW


async def test_batch_meets_tenant_throughput_floor(
    pool: asyncpg.Pool, kill_switch: KillSwitchService, risk_gate_repo
) -> None:
    """수치 성능/처리량 -- 이 배치는 3600초 스케줄러 tick(background_loops.py)
    안에서 그 시점의 전체 tenant 목록을 순회한다. tenant 수가 늘어도 배치가
    다음 tick 전에 끝나야 하므로, 20 tenant를 추가로 실 DB에 심어 처리하는 데
    걸리는 시간을 tenant당 평균으로 정규화해 하한 처리량(tenant당 0.5s 이하,
    즉 ≥2.0 tenants/s)을 고정한다. 이 파일의 다른 테스트들도 같은
    _BUSINESS_DATE에 tenant를 누적시키므로(DB가 테스트 간에 비워지지 않음)
    고정된 전체 tenant 수가 아니라 `report.tenants_evaluated`로 나눈 평균에
    대해 단언해야 반복 실행에도 흔들리지 않는다."""
    tenant_ids = [await create_test_tenant(pool) for _ in range(20)]
    venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    for tenant_id in tenant_ids:
        order_id = await _seed_order(
            pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED"
        )
        await _seed_fill(pool, order_id, side="BUY", price=Decimal("100"), venue_ts=venue_ts)

    start = time_module.perf_counter()
    report = await run_daily_post_trade_batch(
        pool,
        kill_switch,
        risk_gate_repo,
        business_date=_BUSINESS_DATE,
        now=datetime.now(timezone.utc),
    )
    elapsed = time_module.perf_counter() - start

    assert report.tenants_evaluated >= len(tenant_ids)
    per_tenant_sec = elapsed / report.tenants_evaluated
    assert per_tenant_sec < 0.5, (
        f"{report.tenants_evaluated}-tenant batch averaged {per_tenant_sec:.3f}s/tenant"
    )
    throughput = report.tenants_evaluated / elapsed
    assert throughput >= 2.0, f"throughput {throughput:.2f} tenants/s below the 2.0 floor"


async def test_concurrent_batch_instances_do_not_double_activate(pool: asyncpg.Pool) -> None:
    """D3 다중 인스턴스 -- 스케줄 배치가 동시에 두 프로세스(별도 커넥션 풀 +
    별도 KillSwitchService/RiskGateRepository 인스턴스)에서 같은 tenant/
    business_date를 돌려도, `_activate_if_not_active`의 advisory lock 덕에
    ACTIVE COMPLIANCE 레코드는 정확히 1건만 남는다. 이 락이 없던 이전
    read-then-write 버전은 이 테스트가 간헐적으로 2건을 만들어 실패했다."""
    tenant_id = await create_test_tenant(pool)
    await _seed_order(pool, tenant_id, side="SELL", price=Decimal("100"), status="ACKNOWLEDGED")
    fill_order_id = await _seed_order(
        pool, tenant_id, side="BUY", price=Decimal("100"), status="FILLED"
    )
    venue_ts = datetime.combine(_BUSINESS_DATE, time(12, 0), tzinfo=timezone.utc)
    await _seed_fill(pool, fill_order_id, side="BUY", price=Decimal("100"), venue_ts=venue_ts)

    pool_a = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    pool_b = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    try:
        repo_a = PostgresRiskGateRepository(pool_a)
        repo_b = PostgresRiskGateRepository(pool_b)
        kill_switch_a = KillSwitchService(
            risk_gate_repo=repo_a,
            pg_pool=pool_a,
            paper_control_repo=PostgresPaperControlRepository(pool_a),
            exchange_adapters={},
            audit_repo=PostgresAuditEventRepository(pool_a),
        )
        kill_switch_b = KillSwitchService(
            risk_gate_repo=repo_b,
            pg_pool=pool_b,
            paper_control_repo=PostgresPaperControlRepository(pool_b),
            exchange_adapters={},
            audit_repo=PostgresAuditEventRepository(pool_b),
        )
        now = datetime.now(timezone.utc)
        await asyncio.gather(
            run_daily_post_trade_batch(
                pool_a, kill_switch_a, repo_a, business_date=_BUSINESS_DATE, now=now
            ),
            run_daily_post_trade_batch(
                pool_b, kill_switch_b, repo_b, business_date=_BUSINESS_DATE, now=now
            ),
        )
    finally:
        await pool_a.close()
        await pool_b.close()

    reasons = await _active_compliance_controls(pool, tenant_id)
    assert len(reasons) == 1


def test_background_loops_wires_post_trade_batch() -> None:
    """DoD (e) -- 배선 증명. `background_loops.py`가 CM-11 배치를 실제로
    임포트·스케줄하지 않으면 이 테스트가 실패한다."""
    import src.services.background_loops as module

    assert "run_daily_post_trade_batch" in module.__dict__
    source = module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as f:
        text = f.read()
    assert "run_daily_post_trade_batch(" in text
    assert "post_trade_batch_task" in text
