"""H-7a 백엔드 e2e 3건 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로 옮겨
두므로(레포 전역 관례), 여기서는 다른 `tests/integration/**/conftest.py`와
동일하게 asyncpg DSN 변환 + 풀 픽스처만 둔다."""

from __future__ import annotations

import os

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()
