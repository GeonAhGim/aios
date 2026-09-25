"""AIOS test-process bootstrap.

Integration tests must never read developer or production credentials, and they
must never fall back to ``aios_dev``.  Many existing integration modules read
the project-root ``.env`` through ``dotenv_values``; this bootstrap is imported
before those modules and supplies one deterministic test-only view instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TypeVar

import asyncpg
import dotenv
import pytest

from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.rate_limit.limiter import UnlimitedRateLimiter, set_limiter
from tests.support.db import ensure_worker_database
from tests.support.db import tx_conn as tx_conn  # noqa: F401 -- re-exported fixture

try:
    import psutil
except ImportError:  # pragma: no cover -- psutil is not a declared hard
    # dependency (it happens to be present in some dev venvs as a transitive
    # tool dependency); load reporting degrades to "n/a" without it instead
    # of failing perf tests over a missing optional import.
    psutil = None

coverage: ModuleType | None
try:
    import coverage
except ImportError:  # pragma: no cover -- coverage.py ships with pytest-cov
    # (dev dependency); guard the import so PerfBudget still works in a venv
    # that lacks it instead of failing perf tests over a missing optional dep.
    coverage = None

_T = TypeVar("_T")

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
    # PLT-24 -- kid-rotation key for src/services/auth/tokens.py
    # TokenIssuer/TokenVerifier.from_env() (a separate scheme from the legacy
    # JWT_SECRET_KEY). The .env.example slot is wired by PLT-42.
    "JWT_SIGNING_KEYS": "test1:" + "aa" * 32,
    "JWT_ACTIVE_KID": "test1",
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
# task-1720(P1-A) — OMS outbox 디스패처도 같은 이유로 기본 차단한다: 공유
# TEST_DATABASE_URL에 다른 테스트가 남긴 PENDING outbox 행을 lifespan 통합
# 테스트가 자기도 모르게 실전송(스텁 어댑터 없이)하지 않도록. 디스패처
# 자체는 tests/integration/oms/test_background_loops_wiring.py가 플래그를
# 켜고 직접 호출해 검증한다.
os.environ.setdefault("AIOS_OMS_DISPATCHER_ENABLED", "0")
# task-2358(R-52) — liquidation_executor's worker loop calls a real
# BitgetAdapter.get_ticker() every tick; block it in lifespan integration
# tests for the same reason as the two flags above. Exercised directly by
# tests/integration/risk/test_liquidation_worker.py.
os.environ.setdefault("AIOS_LIQUIDATION_WORKER_ENABLED", "0")
# task-2509(CM-11) — post_trade_batch도 같은 이유로 기본 차단: 공유
# TEST_DATABASE_URL에 다른 테스트가 남긴 fills/orders를 lifespan 통합테스트가
# 자기도 모르게 스캔해 다른 테스트의 tenant에 kill switch를 걸지 않도록.
# 배치 자체는 tests/integration/mandates/test_post_trade_batch.py가 직접 호출해 검증한다.
os.environ.setdefault("AIOS_POST_TRADE_BATCH_ENABLED", "0")
# EM-15 -- algo tick scheduler polls every registered run on its own interval; block it
# in lifespan integration tests for the same reason as the flags above (no algo run is
# ever registered against `tests/conftest.py`'s shared pool, so this would just be a
# no-op poll loop, but it still costs a task/log line every cycle). Exercised directly by
# tests/integration/ems/test_algo_lifecycle.py and tests/unit/foundation/ems/test_start_algo.py.
os.environ.setdefault("AIOS_ALGO_SCHEDULER_ENABLED", "0")
# §10 -- run_liquidation_worker_once() fails closed without this secret;
# tests need a deterministic value, not a real production key.
os.environ.setdefault("AIOS_LIQUIDATION_SEED_KEY", "test-only-liquidation-seed-key")


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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """task-645(esc-ci-532f4f0f5388): `pyproject.toml`의 전역 per-test
    타임아웃(120s)은 행(hang) 하나가 CI 2400s 상한을 통째로 잡아먹지 않도록
    막는 가드다. `perf` 마커 테스트(task-489/LB-18)는 100회 실 DB 왕복을
    반복하는 게 의도된 설계라 이 로컬 공유 Postgres 환경에서는 120s를
    넘기기도 한다 — 행이 아니라 알려진 느림이므로 여기서만 넉넉하게
    재정의한다(테스트 파일 자체는 이 리프 범위 밖이라 마커로만 구분)."""
    for item in items:
        if item.get_closest_marker("perf") is not None:
            item.add_marker(pytest.mark.timeout(600))

    # task-6845(esc-ci-pytest_perf): `ci_recheck.py`의 full 모드는 perf 마커
    # 테스트를 별도 직렬 단계로 돌리려고 CLI에 `-m "perf and not nightly and
    # not live_demo"`를 직접 넘긴다 — pytest는 CLI `-m`이 있으면 그 값이
    # `pyproject.toml` addopts의 `-m "not nightly and not live_demo and not
    # redis"`를 완전히 대체한다(마지막 `-m`만 적용), addopts에 합쳐지지
    # 않는다. 그 CLI 식이 "perf"만 요구하고 "not redis"를 다시 쓰지
    # 않으므로, perf이면서 redis(M2-8 Phase2, 실 Redis 인스턴스 필요)이기도
    # 한 테스트가 걸러지지 않고 새어 들어와 Redis 없는 환경에서 커넥션
    # 실패로 적색이 됐다(`tests/integration/event_bus/
    # test_redis_streams_event_bus.py::test_dispatch_latency_p95_under_ws_fanout_budget`).
    # `-m`을 다시 파싱해 조건을 재구현하는 대신, 호출자의 markexpr에 그
    # 마커 이름이 직접 등장하지 않을 때만 기본 제외를 재적용한다 — 그러면
    # `pytest -m redis ...`처럼 명시적으로 그 마커를 요청한 실행은 여전히
    # 그대로 동작한다.
    markexpr = config.getoption("markexpr") or ""
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        hit_markers = {m.name for m in item.iter_markers()} & _DEFAULT_EXCLUDED_MARKERS
        if hit_markers and not any(name in markexpr for name in hit_markers):
            deselected.append(item)
        else:
            kept.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


# pyproject.toml addopts(`-m "not nightly and not live_demo and not redis"`)가
# 기본 실행에서 항상 제외하는 마커 — pytest_sessionfinish가 "이 마커들 때문에
# 전부 deselect됐다"를 판별할 때 쓰는 것과 동일한 집합이어야 한다.
_DEFAULT_EXCLUDED_MARKERS = frozenset({"nightly", "live_demo", "redis"})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """task-6566(esc-ci-pytest.json) — local_ci의 commit 모드는 커밋이 건드린
    테스트 파일을 직접 pytest 인자로 넘긴다(`ci_impact.select_impacted_tests`,
    `tests/` 아래 변경 파일은 그 자신이 곧바로 impacted가 된다). 그 파일의
    테스트가 전부 `not nightly and not live_demo and not redis`(pyproject.toml
    addopts) 마커로 걸러지면 — 예: `tests/e2e/bitget_demo/
    test_bitget_demo_pipeline.py`(전부 `live_demo`) 하나만 바뀐 커밋 — pytest는
    실제로는 아무 결함도 없는데 `ExitCode.NO_TESTS_COLLECTED`(5)로 끝나
    CI를 영구 적색으로 고착시킨다(esc-ci-pytest.json이 task-6176부터
    task-6566까지 열댓 번 재발한 근본 원인).

    `ExitCode.NO_TESTS_COLLECTED`는 서로 다른 상황을 구분하지 않는다: (a)
    경로/이름이 틀려 애초에 수집된 항목이 0개, (b) `-k`로 사람이 직접 걸러
    실수로 0건이 됐음, (c) 수집된 항목이 전부 addopts의 기본 제외 마커만으로
    deselect됨. (c)만 "이번 변경과 무관한, 의도적으로 기본 실행에서 빠지는
    테스트만 골랐다"는 유효한 결과다 — (a)/(b)는 계속 실패해야 한다. deselect된
    모든 항목이 `_DEFAULT_EXCLUDED_MARKERS` 중 하나를 실제로 달고 있을 때만
    (c)로 판정한다 — (b)처럼 무관한 테스트를 `-k`로 잘못 걸렀다면 그 항목들은
    이 마커가 없으므로 여전히 적색으로 남는다."""
    if exitstatus != pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    deselected = reporter.stats.get("deselected") or []
    if not deselected:
        return
    if all(
        any(m.name in _DEFAULT_EXCLUDED_MARKERS for m in item.iter_markers()) for item in deselected
    ):
        session.exitstatus = pytest.ExitCode.OK


@pytest.fixture(autouse=True)
def _reset_metrics_singleton():
    """`set_metrics`는 프로세스 전역 싱글턴이라, 한 테스트가 대체 구현체로
    바꾸고 복원하지 않으면 같은 워커에서 뒤이어 실행되는 무관한 테스트까지
    그 구현체를 보게 된다. 매 테스트 전후로 `NullMetrics`로 되돌려 순서
    의존성을 없앤다."""
    set_metrics(NullMetrics())
    yield
    set_metrics(NullMetrics())


@pytest.fixture(autouse=True)
def _reset_rate_limiter_singleton():
    """PLT-25 — `set_limiter`도 위 메트릭과 같은 프로세스 전역 싱글턴이다.
    `RateLimitMiddleware`가 모든 요청(라우터 통합테스트 전부 포함)에 걸리므로,
    실제 `InMemoryTokenBucket`을 기본값으로 두면 같은 IP/subject 키를 반복
    사용하는 기존 테스트들이 서로의 버킷을 갉아먹어 무관한 429가 나기
    시작한다. `test_rate_limit_storm.py`만 명시적으로 `InMemoryTokenBucket`을
    다시 꽂아 자기 시나리오를 검증하고, 그 외 전부는 무제한 대역을 본다."""
    set_limiter(UnlimitedRateLimiter())
    yield
    set_limiter(UnlimitedRateLimiter())


@pytest.fixture(autouse=True)
def _isolate_root_logger_state():
    """task-6371/task-6392 — `configure_logging()`(src/core/logging/schema.py)는
    앱 lifespan 시작마다 `root.handlers.clear()` + `root.setLevel(...)`로
    루트 로거를 무조건 재구성하고, `src/main.py`의 lifespan은 반환된
    `QueueListener`를 저장·정지하지 않는다. 이 worktree 안 20개 이상의
    라우터 통합테스트가 `client`(lifespan) 픽스처를 쓰므로, 그런 테스트가
    caplog 단언 테스트보다 같은 xdist 워커 안에서 먼저 돌면 루트 로거의
    핸들러/레벨/propagate가 그 뒤로도 계속 오염된 채 남아 `caplog.records`가
    실행 순서에 따라 비거나 채워지는 flaky를 만든다(3회 연속 적색, 매번 다른
    caplog 테스트가 걸림 — 5904/5924/5935/6322가 그때그때 5건만 고쳤던
    바로 그 근본 원인). task-6392에서 propagate와 하위 로거 상태도 격리한다.
    매 테스트 전후로 스냅샷·복원해 어떤 테스트가 루트 로거를 재구성해도
    다음 테스트로 새지 않게 한다."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_propagate = root.propagate
    # task-7439 — `logging.disable(level)`은 로거별 상태가 아니라
    # `logging.Logger.manager.disable`이라는 프로세스 전역 정수다. 위
    # 핸들러/레벨/propagate/disabled 스냅샷은 로거별 상태만 다뤄 이 값은
    # 놓친다 — 어떤 테스트가 `logging.disable(logging.CRITICAL)`을 호출하고
    # 복원하지 않으면, 같은 xdist 워커에서 그 뒤에 도는 모든 caplog 단언이
    # (레벨/핸들러가 멀쩡해 보여도) 전역 게이트에 막혀 빈 records를 본다.
    original_manager_disable = logging.Logger.manager.disable
    # 모든 활성 로거의 핸들러 상태를 스냅샷 (getLogger()는 이미 생성된 로거만 반환)
    original_logger_states = {}
    for name in list(logging.Logger.manager.loggerDict.keys()):
        try:
            logger = logging.getLogger(name)
            if logger is root:
                continue
            original_logger_states[name] = {
                "handlers": list(logger.handlers),
                "level": logger.level,
                "propagate": logger.propagate,
                # logging.config.fileConfig/dictConfig(disable_existing_loggers=True)는
                # 기존 로거의 disabled 플래그를 켠다 — 핸들러·레벨·propagate만
                # 복원하면 이 오염은 그대로 남는다(src/db/migrations/env.py 사례).
                "disabled": logger.disabled,
            }
        except (AttributeError, RuntimeError):
            # race condition — 다른 스레드가 동시에 로거를 생성/삭제할 수 있음
            pass
    yield
    # 복원
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
    root.propagate = original_propagate
    logging.Logger.manager.disable = original_manager_disable
    for name, state in original_logger_states.items():
        try:
            logger = logging.getLogger(name)
            logger.handlers[:] = state["handlers"]
            logger.setLevel(state["level"])
            logger.propagate = state["propagate"]
            logger.disabled = state["disabled"]
        except (AttributeError, RuntimeError):
            pass


@contextmanager
def paused_coverage() -> Iterator[None]:
    """task-7250(esc-ci-coverage) — `pytest --cov=src`가 걸어 둔 전역
    line-tracer(`sys.settrace`)는 감싼 구간의 모든 CPU/wall 시간 측정에
    실제 오버헤드로 섞여 들어간다. perf 예산 단언은 이 구간에서만 활성
    `coverage.Coverage`를 멈췄다 재개해 트레이서 오버헤드를 측정에서
    제외한다 — `coverage`가 설치돼 있지 않거나 현재 활성 인스턴스가 없으면
    (예: coverage 없이 단독 실행) 아무 것도 하지 않는다. 감싼 코드는
    그대로 실행되므로(트레이싱만 잠시 꺼질 뿐) 라인 자체는 여전히
    실행되고, 다른 테스트가 같은 경로를 exercising하는 한 커버리지에
    영향이 없다."""
    active_cov = coverage.Coverage.current() if coverage is not None else None
    if active_cov is not None:
        active_cov.stop()
    try:
        yield
    finally:
        if active_cov is not None:
            active_cov.start()


@dataclass(frozen=True)
class PerfSample:
    """단일 1회 호출의 측정값. `cpu_ms`가 예산 판정 기준이고, `wall_ms`는
    실패 메시지에서 "이 노력이 얼마나 다른 프로세스에 눌렸는지"를 보여주는
    참고값이다(`cpu_ms` << `wall_ms`면 이 워커가 코어를 뺏겼다는 뜻)."""

    cpu_ms: float
    wall_ms: float
    result: object


class PerfBudget:
    """task-6774 — `perf` 마커(또는 budget/p95/latency 단언) 테스트가 예산
    측정 방식을 파일마다 재구현하지 않도록 공용화한 헬퍼. 측정은 항상
    `time.process_time()`(이 프로세스가 실제로 소비한 CPU 시간)을 기준으로
    한다 — `time.perf_counter()` wall-clock과 달리, 다른 워커 프로세스나 이
    호스트에서 함께 도는 llama.cpp 추론에 코어를 뺏겨 대기한 시간이 계측에
    섞이지 않는다(task-6371이 corporate_actions 테스트 한 곳에만 적용했던
    수법을 모든 perf 테스트로 일반화한 것). `best_of`는 그 위에 best-of-N
    최소값을 더해 노이즈를 한 번 더 걷어낸다; `samples`는 p95/p99처럼
    분포 자체가 필요한 테스트를 위해 원시 샘플 목록을 돌려준다."""

    def sample(self, fn: Callable[[], _T], *, batch: int = 1) -> PerfSample:
        """`batch`>1이면 `fn`을 연속 `batch`회 호출한 총 시간을 `batch`로
        나눠 1회 호출당 시간을 추정한다. `time.process_time()`은 Windows에서
        약 15.6ms(64Hz) 해상도로 양자화된다(`GetProcessTimes` 클록 틱) —
        측정 대상이 그보다 훨씬 빠르면 매 호출이 0ms 또는 15.625ms 중
        하나로만 읽혀 p95/평균이 왜곡된다. `batch`로 여러 호출을 한 구간에
        묶으면 총 CPU 시간이 그 틱 폭보다 커져 양자화 오차가 호출당
        `tick/batch`로 줄어든다(예: batch=8이면 오차가 ~2ms로 줄어든다).

        task-7253(esc-ci-pytest_perf)이 이미 이 메서드에서 coverage
        line-tracer 오버헤드를 측정 구간 밖으로 빼 뒀다(아래 주석). 같은
        근본 원인(esc-ci-coverage)이 `PerfBudget`을 거치지 않고 raw
        `time.perf_counter()`를 직접 쓰는 테스트에도 있어, 그쪽은
        `paused_coverage()`(이 파일 상단)로 동일하게 고쳤다 — task-7250."""
        result: _T | None = None
        # task-7253(esc-ci-pytest_perf): the `pytest_perf` CI step runs with
        # `--cov=src --cov-append` for coverage accounting. coverage.py's line
        # tracer hooks via `sys.settrace()` and adds real per-line CPU work in
        # *this* process, so `time.process_time()` captures tracer overhead
        # alongside the code under test -- a tight inner loop (e.g. a few
        # hundred thousand comparisons) can see its measured cpu_ms multiply
        # under coverage even though the code itself did not regress. Suspend
        # tracing for the timed section only; coverage of the surrounding test
        # body (not the hot loop) is unaffected since it is retraced right
        # after restore.
        active_tracer = sys.gettrace()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        sys.settrace(None)
        try:
            for _ in range(batch):
                result = fn()
        finally:
            sys.settrace(active_tracer)
        cpu_ms = (time.process_time() - cpu_start) * 1000 / batch
        wall_ms = (time.perf_counter() - wall_start) * 1000 / batch
        return PerfSample(cpu_ms=cpu_ms, wall_ms=wall_ms, result=result)

    def samples(
        self, fn: Callable[[], _T], *, n: int, warmup: int = 1, batch: int = 1
    ) -> list[PerfSample]:
        for _ in range(warmup):
            fn()
        return [self.sample(fn, batch=batch) for _ in range(n)]

    def best_of(
        self, fn: Callable[[], _T], *, n: int = 5, warmup: int = 1, batch: int = 1
    ) -> PerfSample:
        candidates = self.samples(fn, n=n, warmup=warmup, batch=batch)
        return min(candidates, key=lambda s: s.cpu_ms)

    def load_percent(self) -> float | None:
        """`os.getloadavg()`는 Windows에 없다 — 이 리포는 Windows 호스트에서도
        돈다(CLAUDE.md 환경 섹션). `psutil.cpu_percent`가 설치돼 있으면 그것을
        쓰고, 없으면 실패 메시지에 부하 정보 없이(`None`) 진행한다(선택적
        의존성, §섹션 상단 import 주석 참조)."""
        if psutil is None:
            return None
        return float(psutil.cpu_percent(interval=None))

    def describe(self, sample: PerfSample, *, budget_ms: float) -> str:
        load = self.load_percent()
        load_str = f"{load:.0f}%" if load is not None else "n/a"
        return (
            f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms "
            f"load={load_str} budget<{budget_ms:.3f}ms"
        )

    def assert_within(
        self,
        fn: Callable[[], _T],
        *,
        budget_ms: float,
        n: int = 5,
        warmup: int = 1,
        batch: int = 1,
        label: str = "",
    ) -> PerfSample:
        sample = self.best_of(fn, n=n, warmup=warmup, batch=batch)
        prefix = f"{label}: " if label else ""
        assert sample.cpu_ms < budget_ms, prefix + self.describe(sample, budget_ms=budget_ms)
        return sample


@pytest.fixture
def perf_budget() -> PerfBudget:
    """task-6774 — perf 마커(또는 budget/p95/latency 단언) 테스트 전수가 이
    픽스처로 측정을 통일한다. 단순 예산 단언은
    `perf_budget.assert_within(fn, budget_ms=...)`, p95 등 분포가 필요하면
    `perf_budget.samples(fn, n=...)`을 직접 쓴다."""
    return PerfBudget()
