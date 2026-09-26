"""FA-3(789c138f13fe) DEEPEN(task-3008) — 부족 증빙 보강.

task-2724 DEPTH 감사(docs/audit/DEPTH_FA.md)가 원 task-1709를 D1로 매긴
근거: `test_migration_fa3_orders_fills_columns.py`(5함수)에 negative가
`test_negative_insert_with_nonexistent_fund_id_rejected_by_fk` 1건뿐이고
(ADR-2026-09-09-C D2 하한은 negative>=3), 성능단언이 없고, FA는 안전축이라
D3(적대/리플레이/다중인스턴스) 증거가 필요한데 전혀 없었다.

이 파일이 새로 증명하는 것(원 파일은 건드리지 않는다 — 이미 정착된 라운드
트립/백필 테스트를 재작성하지 않고 부족분만 보탠다):
  1~3. negative 3건 추가(원 파일의 1건과 합쳐 총 4건): orders.portfolio_id,
     fills.fund_id, fills.portfolio_id 각각의 FK 거부.
  4. 성능단언: 부트스트랩된 사용자 50명의 소급 백필이 예산 안에서 끝난다.
  5. D3 리플레이: downgrade -> upgrade 사이클을 두 번 반복해도 백필 결과값
     (fund_id/portfolio_id)이 동일하다 — `default_fund_id`/`default_portfolio_id`
     가 user_id의 순수 함수(UUIDv5)라는 전제를 실제 마이그레이션 경로로 검증한다.
  6. D3 다중 인스턴스: 마이그레이션 본문의 백필 UPDATE(789c138f13fe:86-97)를
     그대로 재현한 SQL을 같은 대상 행에 대해 여러 커넥션에서 동시에 실행해도
     경합/교착 없이 같은 정답에 수렴한다 — 여러 마이그레이터 인스턴스가 겹쳐
     실행되는(락 획득 전 경합) 상황에 대한 안전성 증명이다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
)
from tests._perf.relative_budget import RelativeBudget
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "c9f4e2a1b6d7"
_PERF_USER_COUNT = 50
_CONCURRENT_RUNNERS = 8
# task-7674: the original absolute 10.0s budget compared `elapsed` to this
# host's clock/disk/Postgres-lock speed, not the backfill code -- it went red
# on a busy/shared host (many concurrent worktrees hitting the same local
# Postgres, cf. scripts/setup_test_db.py's _list_test_databases docstring)
# with nothing in the migration changed (locally observed 5.52s idle vs
# 11.34s while another perf test's migration ran concurrently). Same
# RelativeBudget fix as task-7631: express the budget as a multiple of a
# same-process pure-Python calibration loop. This op is a single
# alembic-subprocess + real-DB migration (can't cheaply repeat for a
# best-of-N -- each sample would re-seed 50 users), so it uses a single
# wall-clock sample (n=1) rather than assert_within's default best-of-5.
# Ratio derivation: worst locally observed elapsed 11.34s against a ~80ms
# calibration (~142x); 300 keeps >2x headroom over that worst sample to
# absorb further shared-Postgres contention while still catching a real
# O(n) -> O(n^2) regression in the backfill.
_PERF_MAX_RATIO = 300.0

# 789c138f13fe:86-97 백필 UPDATE의 사본(asyncpg 위치 파라미터로만 변환).
_BACKFILL_UPDATE_SQL = """
    UPDATE orders SET fund_id = $1, portfolio_id = $2
    WHERE user_id = $3
    AND EXISTS (SELECT 1 FROM fund WHERE fund_id = $1)
    AND EXISTS (
      SELECT 1 FROM portfolio
      WHERE portfolio_id = $2 AND fund_id = $1
    )
    """


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=max(4, _CONCURRENT_RUNNERS))
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _insert_bare_order(conn: asyncpg.Connection, user_id) -> object:
    """FA-3 이전 스키마(fund_id/portfolio_id 없음) 가정 최소 INSERT."""
    return await conn.fetchval(
        """
        INSERT INTO orders (
            user_id, client_order_id, strategy_id, strategy_version, symbol,
            exchange, side, order_type, quantity, status, filled_quantity
        ) VALUES ($1, $2, 'fa3-deepen', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                  'MARKET', 1, 'CREATED', 0)
        RETURNING order_id
        """,
        user_id,
        f"fa3-deepen-{uuid4().hex}",
    )


async def _bootstrap_fund_and_portfolio(pool: asyncpg.Pool, user_id, *, venue_suffix: str):
    repo = PostgresEntityRepository(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=default_entity_id(user_id),
            tenant_id=user_id,
            name="FA-3 DEEPEN Entity",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=default_fund_id(user_id),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    await repo.create_portfolio(
        Portfolio(
            portfolio_id=default_portfolio_id(user_id),
            fund_id=fund.fund_id,
            venue_account_ref=f"fa3-deepen-venue-{venue_suffix}",
        )
    )


async def test_negative_insert_orders_with_nonexistent_portfolio_id_rejected_by_fk(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO orders (
                    user_id, client_order_id, strategy_id, strategy_version, symbol,
                    exchange, side, order_type, quantity, status, filled_quantity,
                    portfolio_id
                ) VALUES ($1, $2, 'fa3-deepen', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                          'MARKET', 1, 'CREATED', 0, $3)
                """,
                user_id,
                f"fa3-deepen-{uuid4().hex}",
                uuid4(),
            )


async def test_negative_insert_fills_with_nonexistent_fund_id_rejected_by_fk(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await _insert_bare_order(conn, user_id)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO fills (
                    provider_fill_id, venue, order_id, exchange_order_id, symbol, side,
                    quantity, price, fee, fee_currency, liquidity, venue_ts, fund_id
                ) VALUES ($1, 'bitget', $2, 'ext-fa3-deepen', 'BTC/USDT', 'BUY', 1, 10000,
                          1, 'USDT', 'TAKER', now(), $3)
                """,
                f"fa3-deepen-fill-{uuid4().hex}",
                order_id,
                uuid4(),
            )


async def test_negative_insert_fills_with_nonexistent_portfolio_id_rejected_by_fk(pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await _insert_bare_order(conn, user_id)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO fills (
                    provider_fill_id, venue, order_id, exchange_order_id, symbol, side,
                    quantity, price, fee, fee_currency, liquidity, venue_ts, portfolio_id
                ) VALUES ($1, 'bitget', $2, 'ext-fa3-deepen', 'BTC/USDT', 'BUY', 1, 10000,
                          1, 'USDT', 'TAKER', now(), $3)
                """,
                f"fa3-deepen-fill-{uuid4().hex}",
                order_id,
                uuid4(),
            )


@pytest.mark.perf
async def test_backfill_of_fifty_bootstrapped_users_completes_within_budget(pool):
    # 성능단언 — 감사가 지적한 "성능단언 없음" 공백을 메운다.
    await purge_position_snapshots(pool)
    _run_alembic("downgrade", _DOWN_REVISION)

    user_ids = []
    async with pool.acquire() as conn:
        for i in range(_PERF_USER_COUNT):
            user_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
            await _bootstrap_fund_and_portfolio(pool, user_id, venue_suffix=str(i))
            await _insert_bare_order(conn, user_id)
            user_ids.append(user_id)

    budget = RelativeBudget()
    sample = budget.measure(lambda: _run_alembic("upgrade", "head"), mode="wall", n=1, warmup=0)
    assert sample.ratio < _PERF_MAX_RATIO, (
        f"{_PERF_USER_COUNT}명 백필: {budget.describe(sample, max_ratio=_PERF_MAX_RATIO)}"
    )

    async with pool.acquire() as conn:
        backfilled = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE user_id = ANY($1::uuid[]) AND fund_id IS NOT NULL",
            user_ids,
        )
    assert backfilled == _PERF_USER_COUNT


async def test_backfill_is_deterministic_across_repeated_downgrade_upgrade_cycles(pool):
    # D3 리플레이 — 같은 사이클을 두 번 반복해도 백필값이 항상 같다.
    bootstrapped_user = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _bootstrap_fund_and_portfolio(pool, bootstrapped_user, venue_suffix="replay")

    results = []
    for _ in range(2):
        await purge_position_snapshots(pool)
        _run_alembic("downgrade", _DOWN_REVISION)
        async with pool.acquire() as conn:
            order_id = await _insert_bare_order(conn, bootstrapped_user)

        _run_alembic("upgrade", "head")

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1", order_id
            )
        results.append((row["fund_id"], row["portfolio_id"]))

    assert results[0] == results[1]
    assert results[0] == (
        default_fund_id(bootstrapped_user),
        default_portfolio_id(bootstrapped_user),
    )


async def test_concurrent_backfill_runners_converge_to_the_same_correct_state(pool):
    # D3 다중 인스턴스 — 여러 마이그레이터 인스턴스가 락 획득 전 겹쳐 같은
    # 백필 UPDATE를 동시에 쏘는 상황을 흉내낸다. 목표 행은 하나뿐이므로
    # "경합 후 정답 하나로 수렴 + 에러/교착 없음"이 곧 안전성 증명이다.
    bootstrapped_user = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _bootstrap_fund_and_portfolio(pool, bootstrapped_user, venue_suffix="concurrent")

    async with pool.acquire() as conn:
        order_id = await _insert_bare_order(conn, bootstrapped_user)

    fund_id = default_fund_id(bootstrapped_user)
    portfolio_id = default_portfolio_id(bootstrapped_user)

    async def _run_once() -> None:
        async with pool.acquire() as conn:
            await conn.execute(_BACKFILL_UPDATE_SQL, fund_id, portfolio_id, bootstrapped_user)

    await asyncio.gather(*(_run_once() for _ in range(_CONCURRENT_RUNNERS)))

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT order_id, fund_id, portfolio_id FROM orders WHERE user_id = $1",
            bootstrapped_user,
        )

    assert len(rows) == 1
    assert rows[0]["order_id"] == order_id
    assert rows[0]["fund_id"] == fund_id
    assert rows[0]["portfolio_id"] == portfolio_id
