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


def test_redis_url_rejects_blank_override() -> None:
    """빈 문자열로 덮어쓴 `TEST_REDIS_URL`을 조용히 기본값으로 되돌리면
    실수로 지운 설정이 들통나지 않는다 — fail-closed로 즉시 거부해야 한다."""
    with pytest.raises(ValueError, match="TEST_REDIS_URL"):
        _redis_url({"TEST_REDIS_URL": ""})


def test_redis_url_rejects_whitespace_only_override() -> None:
    """공백만 있는 값도 빈 문자열과 동치로 취급해 거부해야 한다 — `.strip()`
    없이 `bool("   ")`은 참이라 이 케이스를 놓치기 쉽다."""
    with pytest.raises(ValueError, match="TEST_REDIS_URL"):
        _redis_url({"TEST_REDIS_URL": "   "})


def test_redis_url_rejects_non_redis_scheme() -> None:
    """`redis(s)://`가 아닌 스킴(예: 오타로 들어간 `http://`)은 연결 시점의
    알 수 없는 오류 대신 설정 단계에서 바로 거부되어야 한다."""
    with pytest.raises(ValueError, match="스킴"):
        _redis_url({"TEST_REDIS_URL": "http://localhost:6380/15"})


def test_redis_url_rejects_missing_scheme() -> None:
    """스킴을 통째로 빠뜨린 값(`localhost:6380/15`)도 거부 대상이다 —
    `redis.Redis.from_url`에 그대로 넘기면 모호한 파싱 오류로 번진다."""
    with pytest.raises(ValueError, match="스킴"):
        _redis_url({"TEST_REDIS_URL": "localhost:6380/15"})


def test_redis_url_falls_back_to_default_when_unset() -> None:
    """환경변수가 전혀 없을 때는 기본값을 그대로 쓴다 — 위 negative
    케이스들이 "값이 있지만 잘못됨"과 "값이 없음"을 혼동하지 않는지 확인."""
    assert _redis_url({}) == DEFAULT_TEST_REDIS_URL


async def test_redis_client_impl_propagates_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 — `redis.Redis.from_url`이 던지는 예외(예: 접속 거부)가
    `redis_client` fixture에 삼켜지지 않고 그대로 전파되는지 monkeypatch로
    강제 확인한다(테스트 격리가 깨졌는데도 테스트가 "통과"하면 안 된다)."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise redis.RedisError("connection refused")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_boom))
    with pytest.raises(redis.RedisError, match="connection refused"):
        async for _ in _redis_client_impl(DEFAULT_TEST_REDIS_URL):
            pass
