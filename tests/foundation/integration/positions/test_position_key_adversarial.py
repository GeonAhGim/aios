"""FA-0d D3 증거(적대적/동시성) -- 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(§9 표 113행, DoD "교차 포트폴리오 키 충돌 적대적 테스트").

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1943(commit
278f6227)의 D3 하한 미달로 지적한 공백 중 적대적/동시성 증거를 여기서
메운다(task-3032). 리플레이 증거·성능 단언은 순수 도메인 쪽(I/O 없음)이라
tests/foundation/unit/positions/test_position_key.py에 있다.

위협 시나리오는 `domain/position_key.py` 모듈 독스트링이 이미 명시한다:
"다중 엔티티/다중 포트폴리오 전환 이후, 같은 venue:instrument_id:
strategy_id:execution_id 조합이 서로 다른 포트폴리오에서 동시에 열릴 수
있다(예: 같은 전략을 복제하는 여러 포트폴리오)". `pos_snapshot`의 유일한
제약은 `position_key VARCHAR(200) PRIMARY KEY` 하나뿐이고 `tenant_id`는
PK에 없다(`4a1d0c0de004_positions_journal.py`) -- portfolio_id가 키의
5번째 구성요소가 아니었다면, 이 시나리오는 실제로 서로 다른 포트폴리오의
포지션이 같은 PK에서 충돌해 서로를 덮어쓰는 데이터 손상이었을 것이다.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Portfolio
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, open_position
from tests.support.entities_seed import bootstrap_default_hierarchy

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SHARED_STRATEGY = dict(venue="TESTVENUE", strategy_id="shared-strat", execution_id="paper")


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_portfolio(pool, *, fund_id: UUID) -> UUID:
    repo = PostgresEntityRepository(pool)
    portfolio_id = uuid4()
    await repo.create_portfolio(
        Portfolio(
            portfolio_id=portfolio_id,
            fund_id=fund_id,
            venue_account_ref=f"adversarial-{portfolio_id.hex[:8]}",
        )
    )
    return portfolio_id


async def _fill_once(pool, *, tenant_id: UUID, account_id: UUID, position_key: str) -> None:
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    command = RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.KRW),
        fee=None,
        occurred_at=_OCCURRED_AT,
        trace_id=uuid4(),
    )
    async with pool.acquire() as conn, conn.transaction():
        await record_fill(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=journal,
            snapshots=snapshots,
            audit=audit,
            clock=_clock,
        )


async def _delete_pos_snapshot(pool, *, position_keys: list[str]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pos_snapshot WHERE position_key = ANY($1::varchar[])", position_keys
        )


async def test_identical_strategy_replicated_across_two_portfolios_does_not_collide(pool):
    """적대적 -- 같은 4-part(venue/instrument/strategy/execution)를 서로 다른
    두 포트폴리오가 동시에 쓸 때(전략 복제), portfolio_id가 5번째 구성요소로
    키에 포함돼 있으므로 `pos_snapshot`에 두 개의 독립된 행이 생기고, 한쪽
    포트폴리오의 체결이 다른 쪽 수량을 절대 건드리지 않아야 한다."""
    tenant_id = await create_test_tenant(pool)
    hierarchy = await bootstrap_default_hierarchy(pool, tenant_id)
    account_id = await create_pos_account(pool, tenant_id)
    second_portfolio_id = await _create_portfolio(pool, fund_id=hierarchy.fund.fund_id)

    instrument_id = f"INST{uuid4().hex[:8]}"
    key_a = str(
        PositionKey(
            instrument_id=instrument_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            **_SHARED_STRATEGY,
        )
    )
    key_b = str(
        PositionKey(
            instrument_id=instrument_id, portfolio_id=second_portfolio_id, **_SHARED_STRATEGY
        )
    )
    assert key_a != key_b, "FA-0d의 핵심 불변 -- portfolio_id가 다르면 키도 달라야 한다"

    try:
        await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=key_a)
        await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=key_b)

        await _fill_once(pool, tenant_id=tenant_id, account_id=account_id, position_key=key_a)

        async with pool.acquire() as conn:
            row_a = await conn.fetchrow(
                "SELECT quantity, portfolio_id FROM pos_snapshot WHERE position_key = $1", key_a
            )
            row_b = await conn.fetchrow(
                "SELECT quantity, portfolio_id FROM pos_snapshot WHERE position_key = $1", key_b
            )

        assert row_a is not None and row_b is not None
        assert row_a["portfolio_id"] == hierarchy.portfolio.portfolio_id
        assert row_b["portfolio_id"] == second_portfolio_id
        assert row_a["quantity"] == Decimal("1")
        assert row_b["quantity"] == Decimal("0"), "다른 포트폴리오의 체결이 새어 들어왔다"
    finally:
        await _delete_pos_snapshot(pool, position_keys=[key_a, key_b])


async def test_ten_portfolios_concurrent_fills_for_identical_strategy_stay_isolated(pool):
    """다중 인스턴스/동시성 D3 증거 + 수치 성능 단언 -- 같은 전략을 복제하는
    서로 다른 포트폴리오 10개가 asyncio.gather로 동시에 첫 체결을 기록해도
    각자 자신의 `pos_snapshot` 행(서로 다른 position_key)에만 정확히 수량 1을
    반영해야 한다(교차 오염/락 경합에 의한 유실 없음). 같은 전략인 만큼
    advisory lock 네임스페이스(`hashtext('pos_journal')`)는 공유하지만
    해시 대상 `position_key` 문자열 자체가 portfolio_id로 갈라지므로 서로
    다른 락을 잡는다 -- 이 테스트는 그 격리가 실제로 유지되는지를 확인한다."""
    n = 10
    budget_sec = 10.0
    min_ops_per_sec = 1.0

    tenant_id = await create_test_tenant(pool)
    hierarchy = await bootstrap_default_hierarchy(pool, tenant_id)
    account_id = await create_pos_account(pool, tenant_id)

    instrument_id = f"INST{uuid4().hex[:8]}"
    portfolio_ids = [hierarchy.portfolio.portfolio_id] + [
        await _create_portfolio(pool, fund_id=hierarchy.fund.fund_id) for _ in range(n - 1)
    ]
    keys = [
        str(PositionKey(instrument_id=instrument_id, portfolio_id=pid, **_SHARED_STRATEGY))
        for pid in portfolio_ids
    ]
    assert len(set(keys)) == n, "서로 다른 포트폴리오가 같은 키로 뭉개졌다"

    for key in keys:
        await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=key)

    try:
        start = time.perf_counter()
        results = await asyncio.gather(
            *[
                _fill_once(pool, tenant_id=tenant_id, account_id=account_id, position_key=key)
                for key in keys
            ],
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - start
        ops_per_sec = n / elapsed

        failures = [r for r in results if isinstance(r, BaseException)]
        assert failures == [], f"동시 체결 중 실패 발생: {failures}"

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT position_key, quantity FROM pos_snapshot "
                "WHERE position_key = ANY($1::varchar[])",
                keys,
            )
        quantities = {row["position_key"]: row["quantity"] for row in rows}
        assert len(quantities) == n
        assert all(qty == Decimal("1") for qty in quantities.values()), quantities

        print(
            f"[task-1943/3032 10-portfolio concurrent fills] {n} fills {elapsed:.3f}s "
            f"({ops_per_sec:.1f} ops/s, budget<{budget_sec}s, min>{min_ops_per_sec} ops/s)"
        )
        assert elapsed < budget_sec, (
            f"{n}개 포트폴리오 동시 체결이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
        )
        assert ops_per_sec > min_ops_per_sec, (
            f"동시 체결 처리량이 최소값({min_ops_per_sec} ops/s)에 못 미칩니다({ops_per_sec:.1f})."
        )
    finally:
        await _delete_pos_snapshot(pool, position_keys=keys)
