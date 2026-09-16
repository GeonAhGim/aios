"""M2-8 Phase2 통합테스트 공용 픽스처 — 실 Redis 인스턴스가 필요하다.

이 디렉터리는 `redis` 마커가 붙어 있어(pyproject.toml addopts) 기본 pytest
실행에서 제외된다 — `live_demo`/`nightly`와 같은 관례(인프라 의존 테스트를
`pytest.skip()`으로 조용히 건너뛰는 대신, 애초에 opt-in으로 돌린다 —
`scripts/check_code_ratchets.py`의 skip_xfail 기준선을 건드리지 않기 위함).
명시 실행: `pytest -m redis tests/integration/event_bus/`.

`TEST_REDIS_URL`이 없으면 이 저장소 개발 환경의 `aios-test-redis` 컨테이너
(docker-compose로 6380에 매핑)를 기본값으로 쓴다.
"""

from __future__ import annotations

import os

import pytest
import redis.asyncio as redis

DEFAULT_TEST_REDIS_URL = "redis://localhost:6380/15"


@pytest.fixture(scope="session")
def redis_url() -> str:
    return os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


@pytest.fixture
async def redis_client(redis_url: str):
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
