"""FA-8 negative: `create_order_with_fills` rejects invalid `side` value.

Split from `test_conftest_deepen.py` (task-22195) to reduce pytest-xdist
parallel contention on the original file. This test only validates the
`orders.side` / `fills.side` CHECK constraint — no shared fixtures beyond
`pool` and `build_hierarchy`.

INVARIANTS.md 점검: I-01~I-11은 실행 경로를 다룬다 -- 이 리프는 테스트 전용
DB 픽스처 헬퍼의 입력 검증만 확인하므로 해당 사항 없음(N/A).
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from tests.integration.foundation.allocation.conftest import create_order_with_fills
from tests.integration.foundation.entities.conftest import build_hierarchy


async def test_create_order_with_fills_rejects_invalid_side(pool: asyncpg.Pool) -> None:
    """negative: `orders.side`/`fills.side`는 `CHECK (side IN ('BUY','SELL'))`
    제약을 갖는다(마이그레이션 `210cc26533c7`/`073beca589d5`) -- 임의 문자열은
    거부되어야 한다(게이트-적색 재현: 스키마가 이 방어를 갖지 않으면 조용히
    쓰레기 side 값이 저장된다)."""
    entities = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, entities)

    with pytest.raises(asyncpg.CheckViolationError):
        await create_order_with_fills(
            pool,
            user_id=hierarchy.tenant_id,
            fund_id=hierarchy.fund.fund_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            side="HOLD",
            fills=[(Decimal("10"), Decimal("100.00"))],
        )
