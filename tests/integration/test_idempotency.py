"""15번 §15.1 통합테스트 — 실제 dev DB 대상."""

import asyncio
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.idempotency import DigestMismatchError, with_idempotency


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


# ---------------------------------------------------------------------------
# Negative tests — 불변식 위반 입력이 명시적으로 거부되는지 검증
# ---------------------------------------------------------------------------


async def test_digest_mismatch_raises_error(pool):
    """I-03: 같은 key + 다른 digest → DigestMismatchError (명세 §15.1 규칙).

    최초 호출에서 digest='abc'를 저장한 후, 동일 key로 다른 digest='xyz'를
    보내면 compute() 실행 전에 DigestMismatchError가 발생해야 한다.
    """
    key = f"test-dm-{uuid.uuid4().hex}"

    async def compute():
        return 201, {"id": 1}

    # 첫 호출: digest='abc'로 키 선점
    await with_idempotency(pool, key, compute, tenant_id=None, digest="abc")

    # 두 번째 호출: 다른 digest → 예외 발생
    with pytest.raises(DigestMismatchError) as exc_info:
        await with_idempotency(pool, key, compute, tenant_id=None, digest="xyz")

    assert key in str(exc_info.value)


async def test_non_cacheable_status_codes_not_cached(pool):
    """명세 §15.1: 2xx만 캐시한다. 4xx/5xx는 캐시되지 않아서 동일 key 재호출 시
    compute()가 재실행된다."""
    key = f"test-nc-{uuid.uuid4().hex}"
    calls = []

    async def compute():
        calls.append(1)
        return 400, {"error": "bad request"}

    first_status, _ = await with_idempotency(pool, key, compute)
    assert first_status == 400
    assert len(calls) == 1

    # 같은 key로 재호출 — 400은 캐시되지 않으므로 compute()가 다시 실행됨
    second_status, _ = await with_idempotency(pool, key, compute)
    assert second_status == 400
    assert len(calls) == 2  # compute()가 두 번째로 실행됨

    # 5xx도 동일하게 캐시 안 됨
    key2 = f"test-nc5-{uuid.uuid4().hex}"
    calls2 = []

    async def compute5():
        calls2.append(1)
        return 500, {"error": "server error"}

    await with_idempotency(pool, key2, compute5)
    assert len(calls2) == 1

    await with_idempotency(pool, key2, compute5)
    assert len(calls2) == 2  # 500도 캐시 안 됨


async def test_concurrent_same_key_returns_409(pool):
    """claim-first 원자성: 동일 key로 동시 요청 시 두 번째 요청이 409를 받는다."""
    key = f"test-cc-{uuid.uuid4().hex}"
    results = []

    async def slow_compute():
        await asyncio.sleep(0.3)  # 처리 시간을 길게 가져서 충돌 유도
        return 201, {"id": 1}

    # 두 요청을 동시에 시작
    task1 = asyncio.create_task(with_idempotency(pool, key, slow_compute))
    # 첫 번째가 INSERT DO NOTHING으로 선점한 직후, 두 번째가 같은 key로 진입
    await asyncio.sleep(0.05)
    task2 = asyncio.create_task(with_idempotency(pool, key, slow_compute))

    r1, r2 = await asyncio.gather(task1, task2)
    results.extend([r1, r2])

    # 하나는 201, 다른 하나는 409여야 한다
    status_codes = {r[0] for r in results}
    assert status_codes == {201, 409}


# ---------------------------------------------------------------------------
# 실패주입 — compute() 예외 발생 시 선점 행 자동 해제
# ---------------------------------------------------------------------------


async def test_compute_exception_releases_claim(pool):
    """compute()가 예외를 던지면 선점 행이 DELETE되어 같은 key로 재시도할 수 있다.

    실패주입: monkeypatch로 compute()가 예외를 던지도록 만든 후, 같은 key로
    재호출하면 정상적으로 compute()가 다시 실행되는지 검증한다.
    """
    key = f"test-re-{uuid.uuid4().hex}"
    calls = []

    async def failing_compute():
        calls.append(1)
        raise RuntimeError("simulated failure")

    # 첫 호출: 예외 발생 → 선점 행이 삭제되어야 함
    with pytest.raises(RuntimeError):
        await with_idempotency(pool, key, failing_compute)

    assert len(calls) == 1

    # 같은 key로 재호출 — compute()가 다시 실행되어야 함
    async def success_compute():
        return 201, {"id": 42}

    status, body = await with_idempotency(pool, key, success_compute)
    assert status == 201
    assert body == {"id": 42}
    # failing_compute는 1회, success_compute는 1회 = 총 2회 호출
    assert len(calls) == 1
