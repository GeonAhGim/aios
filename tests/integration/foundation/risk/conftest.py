"""task-6510 통합테스트 공용 `pool` 픽스처 — 다른 `foundation/*` 통합테스트
디렉터리(`tests/integration/foundation/positions/conftest.py` 등)와 동일한
관례: `tests/conftest.py`가 옮겨 둔 `DATABASE_URL`을 asyncpg DSN으로 변환."""
from __future__ import annotations

import os

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=64)
    yield p
    await p.close()
