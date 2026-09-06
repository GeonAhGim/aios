"""`tests/integration/exchanges/` 공용 `pool` 픽스처.

`tests/integration/oms/conftest.py`와 동일 관례 — `paper_sim_accounts`/
`paper_sim_orders`는 `account_id`에 FK가 없어(마이그레이션 d0a580db5ce8
docstring) `create_test_user` 등 선행 픽스처가 필요 없다.
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
