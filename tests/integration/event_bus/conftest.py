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
from collections.abc import AsyncGenerator, Mapping

import pytest
import redis.asyncio as redis

DEFAULT_TEST_REDIS_URL = "redis://localhost:6380/15"


def _redis_url(env: Mapping[str, str]) -> str:
    raw = env.get("TEST_REDIS_URL")
    if raw is None:
        return DEFAULT_TEST_REDIS_URL
    if not raw.strip():
        raise ValueError(
            "TEST_REDIS_URL이 빈 문자열이다 — 비워진 환경변수를 조용히 기본값으로"
            " 되돌리면 실수로 지운 설정을 숨긴다, fail-closed로 즉시 거부한다"
        )
    if not (raw.startswith("redis://") or raw.startswith("rediss://")):
        raise ValueError(f"TEST_REDIS_URL은 redis(s):// 스킴이어야 한다: {raw!r}")
    return raw


@pytest.fixture(scope="session")
def redis_url() -> str:
    return _redis_url(os.environ)


async def _redis_client_impl(url: str) -> AsyncGenerator[redis.Redis, None]:
    client = redis.Redis.from_url(url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncGenerator[redis.Redis, None]:
    async for client in _redis_client_impl(redis_url):
        yield client


# --- DEEPEN(task-10253): conftest 헬퍼/fixture 자체의 negative/실패주입 ------
# 원 리프(task-6704)는 이 공용 fixture 파일에 negative test가 0건이었다 —
# `redis_url`/`redis_client`가 잘못된 환경변수·의존성 장애를 제대로
# 거부/전파하는지는 그동안 어느 `test_*.py`도 직접 검증하지 않았다.
