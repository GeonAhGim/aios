"""FA-14 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 그 값을 asyncpg DSN으로 변환한 `pool` 픽스처만 둔다
(다른 통합테스트 디렉터리들과 동일 관례, 예: `tests/integration/foundation/
ledger/conftest.py`).
"""
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


@pytest.fixture(autouse=True)
async def _ledger_control_clean_slate(pool):
    """`tests/integration/foundation/ledger/conftest.py`의 동명 픽스처와
    같은 이유(다른 디렉터리의 LC-10 테스트가 남긴 `write_frozen` 잔류가
    이 디렉터리의 원장 투영 테스트를 영구히 막지 않도록)."""

    async def _reset() -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL, "
                "frozen_at = NULL WHERE id = 1"
            )

    await _reset()
    yield
    await _reset()
