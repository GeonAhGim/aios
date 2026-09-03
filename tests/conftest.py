"""AIOS test-process bootstrap.

Integration tests must never read developer or production credentials, and they
must never fall back to ``aios_dev``.  Many existing integration modules read
the project-root ``.env`` through ``dotenv_values``; this bootstrap is imported
before those modules and supplies one deterministic test-only view instead.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import dotenv
import pytest

from src.core.observability.metrics import NullMetrics, set_metrics
from tests.support.db import ensure_worker_database
from tests.support.db import tx_conn as tx_conn  # noqa: F401 -- re-exported fixture

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ROOT_ENV_PATH = (_PROJECT_ROOT / ".env").resolve()

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL이 필요합니다. 테스트는 development/production DB에 연결할 수 없습니다."
    )

# PLT-36: pytest-xdist로 `-n`(병렬 워커) 실행 시, 모든 워커가 같은
# TEST_DATABASE_URL을 공유하면 원장 append 시퀀스·시드 계정이 워커 간에
# 서로 오염된다(esc-ci-b120c35c318c). xdist는 워커 서브프로세스마다
# PYTEST_XDIST_WORKER("gw0", "gw1", ...)를 환경변수로 심어 두므로, 픽스처가
# 아니라 이 모듈 임포트 시점(=워커 프로세스 시작 직후, 아직 아무 DB 커넥션도
# 열리기 전)에 워커 전용 DB로 바꿔 낀다. `-n` 없이 실행하면(worker_id="master")
# 아무 것도 복제하지 않고 기존과 동일하게 TEST_DATABASE_URL을 그대로 쓴다.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "master")
if _WORKER_ID != "master":
    _TEST_DATABASE_URL = asyncio.run(ensure_worker_database(_TEST_DATABASE_URL, _WORKER_ID))

# Do not use an operator's credentials even if their shell or local .env has
# them. External exchange calls in tests are mocked; these values exist only so
# the application can construct its validated SecretBundle during router import.
_TEST_ENV = {
    "DATABASE_URL": _TEST_DATABASE_URL,
    "JWT_SECRET_KEY": "aios-test-only-jwt-secret-must-be-at-least-32-bytes",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRE_MINUTES": "60",
    "CREDENTIAL_ENCRYPTION_KEY": "22" * 32,
    "BITGET_API_KEY": "aios-test-only-bitget-key",
    "BITGET_API_SECRET": "aios-test-only-bitget-secret",
    "KIS_APP_KEY": "aios-test-only-kis-key",
    "KIS_APP_SECRET": "aios-test-only-kis-secret",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "FCM_SERVER_KEY": "",
    "APNS_KEY_ID": "",
    "CORS_ALLOWED_ORIGINS": "http://testserver",
}
os.environ.update(_TEST_ENV)

_original_dotenv_values = dotenv.dotenv_values


def _test_dotenv_values(dotenv_path: str | os.PathLike[str] | None = None, *args, **kwargs):
    """Return test-only settings whenever legacy tests request root ``.env``."""
    if dotenv_path is not None and Path(dotenv_path).resolve() == _ROOT_ENV_PATH:
        return dict(_TEST_ENV)
    return _original_dotenv_values(dotenv_path, *args, **kwargs)


dotenv.dotenv_values = _test_dotenv_values

# 전수감사 §3 배선(실행 루프·재시작 복구) — 통합테스트는 lifespan을 통째로 띄우므로
# 공유 dev DB에 남은 RUNNING 실행·미결 주문을 실거래소로 tick/조회하지 않도록 끈다.
# 스케줄러·복구 자체는 test_execution_scheduler.py / test_restart_recovery.py가
# 직접 호출해 검증한다.
os.environ.setdefault("AIOS_EXECUTION_LOOP_ENABLED", "0")
os.environ.setdefault("AIOS_STARTUP_RECOVERY_ENABLED", "0")


async def retry_too_many_connections(factory, *, attempts: int = 6, base_delay: float = 0.5):
    """TEST_DATABASE_URL이 가리키는 Postgres 인스턴스는 이 worktree 전용이 아니라
    다른 worker 프로세스와 `max_connections`를 나눠 쓴다 — 다른 worker의 통합
    테스트가 동시에 몰리면 이쪽 커넥션 시도가 일시적으로
    `asyncpg.exceptions.TooManyConnectionsError`로 거절될 수 있다(esc-ci-de7f42dfb173,
    test_foundation_evidence_router.py/test_users_router.py의 fixture ERROR).
    지수 백오프로 재시도하고, 진짜 커넥션 누수·설정 오류라면 attempts 소진 후
    마지막 예외를 그대로 전파한다."""
    last_exc: asyncpg.exceptions.TooManyConnectionsError | None = None
    for attempt in range(attempts):
        try:
            return await factory()
        except asyncpg.exceptions.TooManyConnectionsError as exc:
            last_exc = exc
            await asyncio.sleep(base_delay * (2**attempt))
    assert last_exc is not None
    raise last_exc


class _RetryingLifespanContext:
    """`app.router.lifespan_context(app)` 진입(내부 asyncpg pool 생성)만
    `retry_too_many_connections`로 감싼다 — 이미 열린 뒤의 동작은 원본
    context manager에 그대로 위임한다."""

    def __init__(self, app) -> None:
        self._app = app
        self._ctx = None

    async def __aenter__(self):
        async def _enter():
            ctx = self._app.router.lifespan_context(self._app)
            await ctx.__aenter__()
            return ctx

        self._ctx = await retry_too_many_connections(_enter)
        return self._ctx

    async def __aexit__(self, *exc_info):
        assert self._ctx is not None
        return await self._ctx.__aexit__(*exc_info)


def lifespan_context_with_retry(app):
    """라우터 통합테스트의 `client` 픽스처가 쓰는
    `app.router.lifespan_context(app)` 대체 — 동일하게 동작하되 진입 시
    `TooManyConnectionsError`를 재시도한다."""
    return _RetryingLifespanContext(app)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """task-645(esc-ci-532f4f0f5388): `pyproject.toml`의 전역 per-test
    타임아웃(120s)은 행(hang) 하나가 CI 2400s 상한을 통째로 잡아먹지 않도록
    막는 가드다. `perf` 마커 테스트(task-489/LB-18)는 100회 실 DB 왕복을
    반복하는 게 의도된 설계라 이 로컬 공유 Postgres 환경에서는 120s를
    넘기기도 한다 — 행이 아니라 알려진 느림이므로 여기서만 넉넉하게
    재정의한다(테스트 파일 자체는 이 리프 범위 밖이라 마커로만 구분)."""
    for item in items:
        if item.get_closest_marker("perf") is not None:
            item.add_marker(pytest.mark.timeout(600))


@pytest.fixture(autouse=True)
def _reset_metrics_singleton():
    """`set_metrics`는 프로세스 전역 싱글턴이라, 한 테스트가 대체 구현체로
    바꾸고 복원하지 않으면 같은 워커에서 뒤이어 실행되는 무관한 테스트까지
    그 구현체를 보게 된다. 매 테스트 전후로 `NullMetrics`로 되돌려 순서
    의존성을 없앤다."""
    set_metrics(NullMetrics())
    yield
    set_metrics(NullMetrics())
