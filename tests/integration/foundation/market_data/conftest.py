"""LA-12 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로(테스트 전용 DB), 여기서는 그 값을 그대로 읽어 asyncpg DSN으로
변환하고 커넥션 풀만 연다(`tests/integration/foundation/ledger/conftest.py`와
동일 패턴).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import asyncpg
import pytest

if TYPE_CHECKING:
    from src.foundation.market_data.adapters.postgres_batch_repository import (
        PostgresBatchRepository,
    )
    from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool[asyncpg.Connection]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


# CTO 2026-09-23: test_candle_store.py 분할(loc_over_500)로 두 모듈이 공유하는 픽스처.
@pytest.fixture
def candle_store(pool: asyncpg.Pool[asyncpg.Connection]) -> PostgresCandleStore:
    from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore

    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool: asyncpg.Pool[asyncpg.Connection]) -> PostgresBatchRepository:
    from src.foundation.market_data.adapters.postgres_batch_repository import (
        PostgresBatchRepository,
    )

    return PostgresBatchRepository(pool)
