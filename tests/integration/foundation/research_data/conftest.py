"""RD-20 통합테스트 공용 픽스처 — `tests/integration/foundation/market_data/conftest.py`와
동일 패턴(`TEST_DATABASE_URL` -> asyncpg pool)."""
from __future__ import annotations

import os

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()
