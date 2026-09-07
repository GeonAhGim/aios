"""FA-16 `tests/adversarial/eventstore/` 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로(테스트 전용 DB), 여기서는 그 값을 asyncpg DSN으로 변환해
`pool`만 제공한다 — 다른 통합테스트 conftest(예: tests/integration/oms/
conftest.py)와 동일한 관례.
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
