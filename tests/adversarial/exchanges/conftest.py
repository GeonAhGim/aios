"""`tests/adversarial/exchanges/` 공용 `pool` 픽스처(DROP 주입 테스트용).

`tests/integration/exchanges/conftest.py`와 동일 관례.
"""
from __future__ import annotations

import os

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()
