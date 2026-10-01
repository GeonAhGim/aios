"""LB-15 `compute_daily_nav` 동시성(asyncio.gather) — 실제 DB 경합.

`test_compute_daily_nav.py`에서 분리(task-10197, 675줄 LOC 규율 초과).
같은 `(account_id, nav_date)`에 실제 동시 커넥션으로 경합해 (a) 같은
계산의 재시도는 단일 행으로 수렴하고 (b) 서로 다른 유효 계산이 부딪히면
하나만 저장되고 나머지는 어댑터의 진짜 `source_hash` 충돌 경로
(`postgres_nav_repository.NavChainBrokenError`)로 거부됨을 증명한다 —
순차 호출로는 드러나지 않는 실제 DB 경합 경로다.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import asyncpg

from src.foundation.positions.adapters.postgres_nav_repository import (
    NavChainBrokenError as AdapterNavChainBrokenError,
)
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import compute_daily_nav
from src.foundation.positions.contracts.v1 import NAVSnapshot
from tests.integration.foundation.positions._compute_daily_nav_fixtures import (
    BITGET,
    NOW,
    FakeCashSource,
    FakeFxRateSource,
    cmd,
    setup_account,
)


async def test_concurrent_same_day_retries_converge_to_single_row(pool: asyncpg.Pool) -> None:
    """같은 커맨드(같은 source_hash)를 진짜 동시 커넥션으로 여러 번 실행해도
    `pos_nav_daily`에는 한 행만 남는다 — 순차 재실행 멱등(위 테스트)과 달리
    실제 DB 레벨 경합에서도 멱등이 유지되는지를 증명한다."""
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("777"))
    nav_repo = PostgresNavRepository(pool)
    command = cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="777")
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    results = await asyncio.gather(*(compute_daily_nav(command, **common) for _ in range(8)))

    assert all(r.source_hash == results[0].source_hash for r in results)
    assert all(r.closing_nav == Decimal("777") for r in results)
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            results[0].nav_date,
        )
    assert count == 1


async def test_concurrent_different_valid_computations_one_wins_one_rejected(
    pool: asyncpg.Pool,
) -> None:
    """서로 다른(각자 self-consistent한) 두 계산이 같은 날짜를 놓고 진짜
    동시 커넥션으로 경합하면, 정확히 하나만 저장되고 나머지는 어댑터의
    `source_hash` 불일치 경로(`AdapterNavChainBrokenError`)로 거부된다 —
    순차 호출로는 관찰할 수 없는 실제 UNIQUE 제약 레이스다."""
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )
    # 둘 다 opening(0)+realized+funding = closing(cash 1000 + mv 0)를 만족하는
    # self-consistent한 계산이지만, (realized, funding) 조합이 달라 source_hash가
    # 다르다.
    cmd_a = cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="1000")
    cmd_b = cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="500", funding="500")

    results = await asyncio.gather(
        compute_daily_nav(cmd_a, **common),
        compute_daily_nav(cmd_b, **common),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, NAVSnapshot)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"정확히 하나만 저장돼야 합니다: {results}"
    assert len(failures) == 1
    assert isinstance(failures[0], AdapterNavChainBrokenError)

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, successes[0].nav_date)
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            successes[0].nav_date,
        )
    assert stored is not None
    assert stored.source_hash == successes[0].source_hash
    assert count == 1
