"""15번 §15.1 통합테스트 — 실제 dev DB 대상."""
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.idempotency import with_idempotency


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def test_first_call_executes_compute(pool):
    key = f"test-{uuid.uuid4().hex}"
    calls = []

    async def compute():
        calls.append(1)
        return 201, {"id": 1}

    status_code, body = await with_idempotency(pool, key, compute)

    assert status_code == 201
    assert body == {"id": 1}
    assert len(calls) == 1


async def test_repeated_call_with_same_key_does_not_recompute(pool):
    key = f"test-{uuid.uuid4().hex}"
    calls = []

    async def compute():
        calls.append(1)
        return 201, {"id": len(calls)}

    first = await with_idempotency(pool, key, compute)
    second = await with_idempotency(pool, key, compute)

    assert first == second
    assert len(calls) == 1  # 두 번째 호출에서는 compute()가 재실행되지 않음


async def test_different_keys_execute_independently(pool):
    async def compute_a():
        return 201, {"id": "a"}

    async def compute_b():
        return 201, {"id": "b"}

    result_a = await with_idempotency(pool, f"test-a-{uuid.uuid4().hex}", compute_a)
    result_b = await with_idempotency(pool, f"test-b-{uuid.uuid4().hex}", compute_b)

    assert result_a[1]["id"] == "a"
    assert result_b[1]["id"] == "b"
