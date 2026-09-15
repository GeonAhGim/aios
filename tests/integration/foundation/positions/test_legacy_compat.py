"""LegacyPositionsProjection 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-10.
DoD(task-376): 동일 계정·심볼에 대해 legacy `positions` 직접 쿼리 결과와
`LegacyPositionsProjection` 투영 결과가 수량·평단·실현손익까지 일치
(부분청산 후 포함), 대응 legacy 행이 없으면 빈 결과(예외 아님).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id
from src.foundation.positions.adapters.legacy_positions_projection import (
    LegacyPositionsProjection,
)
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account, force_row_replace

_EXCHANGE = "TESTEX"
_EXCHANGE_B = "TESTEX-B"


@pytest.fixture
def projection() -> LegacyPositionsProjection:
    return LegacyPositionsProjection()


async def _seed_linked_pair(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    realized_pnl: Decimal = Decimal("0"),
    closed_at: datetime | None = None,
    exchange: str = _EXCHANGE,
) -> tuple[int, str]:
    """legacy `positions` 행 + `legacy_position_id`로 그 행을 가리키는
    `pos_snapshot` 행을 짝으로 만든다(어댑터 대신 픽스처가 직접 INSERT
    — LB-9 `upsert`는 `legacy_position_id`를 채우지 않음)."""
    async with pool.acquire() as conn:
        legacy_id: int = await conn.fetchval(
            """
            INSERT INTO positions (
                user_id, symbol, exchange, strategy_id, quantity,
                average_entry_price, realized_pnl, entry_time, closed_at
            ) VALUES ($1, $2, $3, 'test-strategy', $4, $5, $6, now(), $7)
            RETURNING id
            """,
            tenant_id,
            symbol,
            exchange,
            quantity,
            price,
            realized_pnl,
            closed_at,
        )
        position_key = _snapshot_key(tenant_id, venue=exchange)
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at,
                fund_id, portfolio_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 'FIFO', $7::jsonb, $8, NULL, 0, 0,
                NULL, NULL, 1, $9, now(), $10, $11
            )
            """,
            position_key,
            tenant_id,
            account_id,
            uuid.uuid4(),
            quantity,
            price,
            json.dumps([]),
            realized_pnl,
            legacy_id,
            default_fund_id(tenant_id),
            default_portfolio_id(tenant_id),
        )
    return legacy_id, position_key


def _snapshot_key(tenant_id: UUID, *, venue: str = _EXCHANGE) -> str:
    # FA-0d-fix: raw fixture rows must be re-keyable by cdb114b6903f on a
    # migration round trip -- 5-part key + real default portfolio (FK).
    return str(
        PositionKey(
            venue=venue,
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="test-strategy",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )


async def _direct_legacy_query(
    pool: asyncpg.Pool, *, user_id: UUID, symbol: str
) -> list[asyncpg.Record]:
    """구 경로 대조군 — 어댑터 없이 `positions`를 직접 읽는다."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id AS legacy_position_id, quantity, average_entry_price,
                   realized_pnl, unrealized_pnl, closed_at
            FROM positions
            WHERE user_id = $1 AND symbol = $2 AND exchange = $3
            ORDER BY entry_time ASC
            """,
            user_id,
            symbol,
            _EXCHANGE,
        )


async def _setup_account(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return tenant_id, account_id


async def _project(projection: LegacyPositionsProjection, pool, *, user_id, symbol):
    async with pool.acquire() as conn, conn.transaction():
        return await projection.get_positions(
            conn, user_id=user_id, symbol=symbol, exchange=_EXCHANGE
        )


async def test_open_position_matches_legacy_query(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2.5"),
        price=Decimal("100.1234567890"),
    )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    legacy_row, projected_row = legacy_rows[0], projected[0]
    assert projected_row.legacy_position_id == legacy_row["legacy_position_id"]
    assert projected_row.quantity == legacy_row["quantity"]
    assert projected_row.average_entry_price == legacy_row["average_entry_price"]
    assert projected_row.realized_pnl == legacy_row["realized_pnl"]
    assert projected_row.unrealized_pnl == legacy_row["unrealized_pnl"]


async def test_partial_close_still_matches_legacy_query(pool, projection):
    """부분청산 후: 수량이 줄고 실현손익이 누적된 상태도 일치해야 한다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id, position_key = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("10"),
        price=Decimal("50"),
    )

    # 부분청산: 10 중 4를 60에 매도 → 남은 수량 6, 실현손익 = 4*(60-50) = 40
    partial_quantity, partial_realized = Decimal("6"), Decimal("40")
    await force_row_replace(
        pool,
        table="positions",
        id_column="id",
        id_value=legacy_id,
        quantity=partial_quantity,
        realized_pnl=partial_realized,
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=partial_quantity,
        realized_pnl_base=partial_realized,
        last_journal_seq=2,
    )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    assert projected[0].quantity == partial_quantity == legacy_rows[0]["quantity"]
    assert projected[0].realized_pnl == partial_realized == legacy_rows[0]["realized_pnl"]
    assert projected[0].average_entry_price == legacy_rows[0]["average_entry_price"]


async def test_closed_position_reports_closed_at_and_matches_legacy(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("0"),
        price=Decimal("20"),
        realized_pnl=Decimal("15"),
        closed_at=datetime.now(timezone.utc),
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].closed_at is not None
    assert projected[0].realized_pnl == Decimal("15")


async def test_no_linked_legacy_row_returns_empty_not_exception(pool, projection):
    """스냅샷이 있어도 `legacy_position_id`가 비어 있으면(아직 연결 안 됨)
    빈 리스트를 반환한다 — 예외가 아니다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at,
                fund_id, portfolio_id
            ) VALUES (
                $1, $2, $3, $4, 3, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                NULL, NULL, 1, NULL, now(), $6, $7
            )
            """,
            _snapshot_key(tenant_id),
            tenant_id,
            account_id,
            uuid.uuid4(),
            json.dumps([]),
            default_fund_id(tenant_id),
            default_portfolio_id(tenant_id),
        )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)
    assert projected == []


async def test_no_matching_symbol_returns_empty_not_exception(pool, projection):
    """해당 계정·심볼 조합의 legacy 행이 아예 없으면 빈 리스트."""
    tenant_id, _ = await _setup_account(pool)
    projected = await _project(projection, pool, user_id=tenant_id, symbol="NEVER-EXISTED")
    assert projected == []


async def test_dangling_legacy_position_id_rejected_by_fk_constraint(pool):
    """`pos_snapshot.legacy_position_id`는 DEFERRABLE FK로 `positions(id)`를
    참조한다(마이그레이션 `a2c4f9e1b3d5`) — 존재하지 않는 id를 가리키는
    스냅샷은 커밋 시 거부된다. 어댑터 모듈 docstring이 전제하는 "INNER
    JOIN이 조용히 빠뜨리는 건 아직 연결 안 된(`legacy_position_id IS
    NULL`) 스냅샷뿐, 댕글링 참조는 애초에 존재할 수 없다"를 raise로
    증명하는 진짜 negative 테스트(이전엔 빈 결과 확인뿐이었다)."""
    tenant_id, account_id = await _setup_account(pool)
    nonexistent_legacy_id = 2_147_483_647

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO pos_snapshot (
                    position_key, tenant_id, account_id, instrument_id, quantity,
                    avg_cost, cost_method, lots, realized_pnl_base,
                    unrealized_pnl_base, fees_base, funding_base, mark_price,
                    mark_at, last_journal_seq, legacy_position_id, updated_at,
                    fund_id, portfolio_id
                ) VALUES (
                    $1, $2, $3, $4, 1, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                    NULL, NULL, 1, $6, now(), $7, $8
                )
                """,
                _snapshot_key(tenant_id),
                tenant_id,
                account_id,
                uuid.uuid4(),
                json.dumps([]),
                nonexistent_legacy_id,
                default_fund_id(tenant_id),
                default_portfolio_id(tenant_id),
            )


class _FaultInjectingConnection:
    """`asyncpg.Connection`을 흉내내 `fetch`에서 항상 실패하는 가짜 커넥션."""

    async def fetch(self, *args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("boom: simulated connection fault")


async def test_get_positions_propagates_connection_fault_without_swallowing(projection):
    """`conn.fetch`가 실패하면 어댑터가 삼키지 않고 그대로 전파해야
    한다(fail-closed, 실패 주입) — 이전엔 정상 경로만 검증했다."""
    with pytest.raises(RuntimeError, match="boom"):
        await projection.get_positions(
            _FaultInjectingConnection(),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            symbol="ANY",
            exchange=_EXCHANGE,
        )


async def test_get_positions_completes_within_latency_budget_for_many_reentries(pool, projection):
    """legacy 재진입(같은 계정·심볼, 서로 다른 `entry_time`)이 300건 쌓인
    계정에서도 조회가 예산 안에 끝나야 한다(수치 성능 단언) — 이전엔 DoD
    "쿼리 결과 동일"만 확인했지 비용은 잰 적이 없었다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    base_time = datetime.now(timezone.utc)
    reentry_count = 300

    async with pool.acquire() as conn:
        for i in range(reentry_count):
            legacy_id: int = await conn.fetchval(
                """
                INSERT INTO positions (
                    user_id, symbol, exchange, strategy_id, quantity,
                    average_entry_price, realized_pnl, entry_time, closed_at
                ) VALUES ($1, $2, $3, 'test-strategy', 1, 10, 0, $4, NULL)
                RETURNING id
                """,
                tenant_id,
                symbol,
                _EXCHANGE,
                base_time + timedelta(microseconds=i),
            )
            await conn.execute(
                """
                INSERT INTO pos_snapshot (
                    position_key, tenant_id, account_id, instrument_id, quantity,
                    avg_cost, cost_method, lots, realized_pnl_base,
                    unrealized_pnl_base, fees_base, funding_base, mark_price,
                    mark_at, last_journal_seq, legacy_position_id, updated_at,
                    fund_id, portfolio_id
                ) VALUES (
                    $1, $2, $3, $4, 1, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                    NULL, NULL, 1, $6, now(), $7, $8
                )
                """,
                _snapshot_key(tenant_id),
                tenant_id,
                account_id,
                uuid.uuid4(),
                json.dumps([]),
                legacy_id,
                default_fund_id(tenant_id),
                default_portfolio_id(tenant_id),
            )

    start = time.perf_counter()
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)
    elapsed = time.perf_counter() - start

    assert len(projected) == reentry_count
    assert elapsed < 1.0, (
        f"get_positions took {elapsed:.3f}s for {reentry_count} re-entries, budget 1.0s"
    )


async def test_different_exchange_same_symbol_not_leaked(pool, projection):
    """같은 테넌트·심볼이라도 거래소가 다르면 섞이면 안 된다 — SELECT의
    `p.exchange = $3` 필터가 빠지는 회귀가 나면 이 테스트가 그 자리에서
    적색이 된다(게이트 적색 재현)."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id_a, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2"),
        price=Decimal("20"),
        exchange=_EXCHANGE_B,
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].legacy_position_id == legacy_id_a
    assert projected[0].exchange == _EXCHANGE


async def test_concurrent_queries_for_different_tenants_stay_isolated(pool, projection):
    """두 테넌트가 같은 심볼을 동시에 조회해도 서로의 포지션이 섞이지
    않는다(적대적/동시성 증명) — 커넥션 풀을 공유하는 동시 조회가 테넌트
    필터를 우회하지 않는지 확인한다."""
    tenant_a, account_a = await _setup_account(pool)
    tenant_b, account_b = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_a, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_a,
        account_id=account_a,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )
    legacy_b, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_b,
        account_id=account_b,
        symbol=symbol,
        quantity=Decimal("2"),
        price=Decimal("20"),
    )

    tenants = [tenant_a if i % 2 == 0 else tenant_b for i in range(20)]
    results = await asyncio.gather(
        *[_project(projection, pool, user_id=tenant, symbol=symbol) for tenant in tenants]
    )

    for tenant, projected in zip(tenants, results, strict=True):
        expected_id = legacy_a if tenant == tenant_a else legacy_b
        assert len(projected) == 1
        assert projected[0].legacy_position_id == expected_id
