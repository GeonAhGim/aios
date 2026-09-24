"""PLT-36 tests/support/db.py — negative/failure-injection/perf/gate tests.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.4/§9 PLT-36.
DoD: negative test ≥3, failure-injection ≥1, perf assertion, gate-red repro.
"""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING

import asyncpg
import pytest

from scripts import setup_test_db as setup_test_db_cli
from tests.support.db import (
    _asyncpg_dsn,
    _db_name,
    _pool_retry_delay,
    _sleep_before_pool_retry,
    _with_database,
    create_pool_with_retry,
    ensure_worker_database,
    session_database_url,
)

if TYPE_CHECKING:
    pass

# ── Negative tests: invalid inputs rejected ──────────────────────────


def test_session_database_url_rejects_uppercase_db_name() -> None:
    """DB 이름에 대문자가混入되면 ValueError로 거부한다."""
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url("postgresql://user:pass@localhost/MyDB", "gw0")


def test_session_database_url_rejects_special_chars_in_db_name() -> None:
    """DB 이름에 특수문자(-)가 있으면 ValueError로 거부한다."""
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url("postgresql://user:pass@localhost/my-db", "gw0")


def test_session_database_url_rejects_db_name_exceeding_40_chars() -> None:
    """DB 이름이 40자를 초과하면 ValueError로 거부한다."""
    long_name = "a" * 41
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url(f"postgresql://user:pass@localhost/{long_name}", "gw0")


def test_session_database_url_rejects_long_worker_db_name() -> None:
    """템플릿+워커 접미사가 40자를 초과하면 ValueError로 거부한다."""
    # template 36 chars + "_gw0" = 40 chars exactly — OK
    # template 37 chars + "_gw0" = 41 chars — should fail
    long_template = "a" * 37
    with pytest.raises(ValueError, match="워커 DB 이름이 규칙"):
        session_database_url(f"postgresql://user:pass@localhost/{long_template}", "gw0")


def test_session_database_url_master_returns_template_unchanged() -> None:
    """worker_id == "master"면 template URL을 그대로 반환한다."""
    url = "postgresql://user:pass@localhost:5432/aios_test"
    assert session_database_url(url, "master") == url


def test_db_name_strips_leading_slash() -> None:
    """urlsplit path의 선행 슬래시를 제거한다."""
    assert _db_name("postgresql://u:p@localhost/aios") == "aios"
    assert _db_name("postgresql://u:p@localhost//aios") == "aios"


def test_with_database_replaces_database_path() -> None:
    """기존 database 경로를 새 이름으로 교체한다."""
    base = "postgresql://user:pass@localhost:5432/template_db"
    result = _with_database(base, "worker_db")
    assert result == "postgresql://user:pass@localhost:5432/worker_db"


def test_asyncpg_dsn_strips_plus_asyncpg_scheme() -> None:
    """postgresql+asyncpg:// → postgresql:// 변환한다."""
    assert _asyncpg_dsn("postgresql+asyncpg://u:p@localhost/db") == "postgresql://u:p@localhost/db"
    # 일반 postgresql://은 변경 없음
    assert _asyncpg_dsn("postgresql://u:p@localhost/db") == "postgresql://u:p@localhost/db"


# ── Failure-injection test: dependency exception ─────────────────────


@pytest.mark.asyncio
async def test_ensure_worker_database_propagates_connect_failure() -> None:
    """asyncpg.connect가 예외를 raise하면 전파된다 — 조용히 폴백하지 않는다.

    미확인 가정(모듈 docstring 참조): 템플릿 DB에 활성 커넥션이 남아 있으면
    Postgres가 CREATE DATABASE ... TEMPLATE를 거부한다. 이 모듈은 그 경우
    예외를 그대로 전파한다 — 조용히 템플릿 DB로 폴백해 격리를 깨뜨리지 않는다.
    """
    template_url = "postgresql+asyncpg://user:pass@localhost:5432/aios_test"

    async def _fake_connect(*args: object, **kwargs: object) -> None:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("connection does not exist")

    # Monkeypatch asyncpg.connect via module __dict__ to avoid mypy assignment
    # type error (Callable vs asyncpg.connect signature mismatch).
    import sys

    db_module = sys.modules["tests.support.db"]
    original_connect = asyncpg.connect
    try:
        db_module.asyncpg.connect = _fake_connect  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await ensure_worker_database(template_url, "gw0")
    finally:
        db_module.asyncpg.connect = original_connect


@pytest.mark.asyncio
async def test_ensure_worker_database_propagates_object_in_use_after_retries() -> None:
    """pg_terminate_backend가 커넥션 종료를 실패하면 ObjectInUseError를 최종 raise한다.

    ensure_worker_database는 최대 5회 지수 백오프로 재시도하지만, 템플릿 DB에
    활성 커넥션이 계속 남아 있으면 결국 예외를 전파한다.
    """
    template_url = "postgresql+asyncpg://user:pass@localhost:5432/aios_test"

    class _FakeConnection:
        """가짜 asyncpg connection. terminate/execute 성공,
        drop/create는 ObjectInUseError."""

        async def execute(self, sql: str, *args: object, **kwargs: object) -> str:
            # pg_terminate_backend 호출은 "성공" (行数 반환)
            # DROP DATABASE 및 CREATE DATABASE 호출은 ObjectInUseError
            if "DROP" in sql or "CREATE" in sql:
                raise asyncpg.exceptions.ObjectInUseError(
                    "source database is being accessed by other users"
                )
            return "0"

        async def close(self) -> None:
            pass

    db_module2 = sys.modules["tests.support.db"]

    original_connect = asyncpg.connect

    async def _fake_connect2(*args: object, **kwargs: object) -> _FakeConnection:
        return _FakeConnection()

    try:
        db_module2.asyncpg.connect = _fake_connect2  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(asyncpg.exceptions.ObjectInUseError):
            await ensure_worker_database(template_url, "gw0")
    finally:
        db_module2.asyncpg.connect = original_connect


@pytest.mark.asyncio
async def test_create_pool_with_retry_retries_transient_reset_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """esc-ci-pytest.json/task-6235: a transient WinError 64 / asyncpg
    ConnectionDoesNotExistError on the first attempt is absorbed -- the second
    attempt's successful pool is returned, not the exception."""
    db_module = sys.modules["tests.support.db"]
    sentinel_pool = object()
    calls = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return sentinel_pool

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert result is sentinel_pool
    assert calls == 2


@pytest.mark.asyncio
async def test_create_pool_with_retry_retries_oserror_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same absorption applies to the raw `OSError` shape (WinError 64
    surfaces as `ConnectionResetError`, an `OSError` subclass, before asyncpg
    wraps it)."""
    db_module = sys.modules["tests.support.db"]
    sentinel_pool = object()
    calls = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(22, "network name no longer available", None, 64, None)
        return sentinel_pool

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    result = await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert result is sentinel_pool
    assert calls == 2


@pytest.mark.asyncio
async def test_create_pool_with_retry_propagates_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: a persistent reset (not just a one-off transient) still
    raises after `_POOL_CONNECT_ATTEMPTS` -- this never becomes a false green."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _always_fails(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise asyncpg.exceptions.ConnectionDoesNotExistError("connection does not exist")

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _always_fails)
    monkeypatch.setattr(db_module, "_POOL_CONNECT_RETRY_BASE_DELAY", 0.0)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert calls == db_module._POOL_CONNECT_ATTEMPTS


@pytest.mark.asyncio
async def test_create_pool_with_retry_does_not_retry_unrelated_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-transient failure (e.g. bad credentials) raises immediately on
    the first attempt -- only the documented transient-reset shape retries."""
    db_module = sys.modules["tests.support.db"]
    calls = 0

    async def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise asyncpg.exceptions.InvalidPasswordError("password authentication failed")

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)

    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert calls == 1


# ── Jitter tests: task-6687/esc-ci-coverage decorrelation ────────────


@pytest.mark.asyncio
async def test_sleep_before_pool_retry_jitters_within_retry_delay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task-6687: `_sleep_before_pool_retry` must sleep
    `random.uniform(0, _pool_retry_delay(attempt))`, not the deterministic
    `_pool_retry_delay(attempt)` itself -- concurrent worktrees sharing one local
    Postgres and computing the same deterministic schedule retry in lockstep and
    repeatedly re-create the contention burst they are backing off from (see
    scripts/replay_verify.py's `_sleep_before_retry`, task-6627, same shape)."""
    db_module = sys.modules["tests.support.db"]
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr(db_module.asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(db_module.random, "uniform", lambda lo, hi: lo + (hi - lo) * 0.25)

    await _sleep_before_pool_retry(3)

    assert captured == [_pool_retry_delay(3) * 0.25]


@pytest.mark.asyncio
async def test_sleep_before_pool_retry_never_exceeds_retry_delay_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative test: across many draws, the jittered sleep must never exceed (or
    go below zero of) the deterministic `_pool_retry_delay(attempt)` it is
    jittering under -- a broken jitter sampling outside
    `[0, _pool_retry_delay(attempt)]` would silently widen the retry budget past
    `_POOL_CONNECT_RETRY_BASE_DELAY`, exactly what DECISION_GUIDELINES B-2 forbids."""
    db_module = sys.modules["tests.support.db"]
    captured: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr(db_module.asyncio, "sleep", _capture_sleep)

    cap = _pool_retry_delay(4)
    for _ in range(200):
        await _sleep_before_pool_retry(4)

    assert all(0.0 <= delay <= cap for delay in captured)


@pytest.mark.asyncio
async def test_create_pool_with_retry_jitters_between_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_pool_with_retry` sleeps via the jittered helper, not a raw
    `asyncio.sleep(_pool_retry_delay(...))` call -- a regression back to the
    deterministic sleep would reintroduce the lockstep thundering herd this
    fix decorrelates."""
    db_module = sys.modules["tests.support.db"]
    calls = 0
    sleeps: list[int] = []

    async def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                "connection was closed in the middle of operation"
            )
        return object()

    async def _fake_sleep_before_pool_retry(attempt: int) -> None:
        sleeps.append(attempt)

    monkeypatch.setattr(db_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(
        db_module, "_sleep_before_pool_retry", _fake_sleep_before_pool_retry
    )

    await create_pool_with_retry("postgresql://u:p@localhost/db")

    assert sleeps == [0]


# ── Boundary tests: valid inputs pass through ────────────────────────


def test_session_database_url_valid_name_concats_worker_suffix() -> None:
    """유효한 DB 이름에 워커 접미사가 올바르게 붙는다."""
    result = session_database_url("postgresql://user:pass@localhost:5432/aios_test", "gw0")
    assert "aios_test_gw0" in result
    assert result != "postgresql://user:pass@localhost:5432/aios_test"


def test_db_name_with_port_in_url() -> None:
    """포트 번호가 URL에 포함되어도 DB 이름만 추출한다."""
    assert _db_name("postgresql://u:p@localhost:5432/mydb") == "mydb"


def test_with_database_preserves_query_and_fragment() -> None:
    """_with_database는 query와 fragment를 보존한다."""
    base = "postgresql://u:p@localhost:5432/db?sslmode=require#frag"
    result = _with_database(base, "newdb")
    assert result == "postgresql://u:p@localhost:5432/newdb?sslmode=require#frag"


# ── Perf assertion: real-DB clone latency (실 DB, TEST_DATABASE_URL) ──


@pytest.mark.perf
@pytest.mark.asyncio
async def test_ensure_worker_database_clone_meets_latency_budget() -> None:
    """`CREATE DATABASE ... TEMPLATE` 복제가 예산 내에 끝난다.

    모듈 docstring은 "마이그레이션 재실행 없이 ~1초"를 주장한다 — 이 테스트는
    TEST_DATABASE_URL을 템플릿으로 실제 워커 DB 하나를 복제해 그 주장을
    실측으로 검증한다. 절대 ms 임계 대신 여유 있는 예산(10s)을 쓰는 이유는
    `test_perf_journal.py`(task-920/1029)와 동일 — 이 리포의 공유 로컬
    Postgres는 CI 환경별 지연 배율 편차가 커서, 좁은 절대 임계는 코드 회귀가
    아니라 환경 변동으로 적색이 된다. 복제된 워커 DB는 측정 직후 DROP해
    다른 세션의 `aios_test_*` 목록을 오염시키지 않는다.
    """
    template_url = os.environ["TEST_DATABASE_URL"]
    worker_id = "permtest"
    budget_sec = 10.0

    start = time.perf_counter()
    worker_url = await ensure_worker_database(template_url, worker_id)
    elapsed = time.perf_counter() - start
    print(f"[PLT-36 clone] ensure_worker_database elapsed={elapsed:.3f}s (budget<{budget_sec}s)")

    admin = await asyncpg.connect(_asyncpg_dsn(_with_database(template_url, "postgres")))
    try:
        worker_db = _db_name(worker_url)
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            worker_db,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{worker_db}"')
    finally:
        await admin.close()

    assert elapsed < budget_sec, f"워커 DB 복제가 예산({budget_sec}s)을 초과: {elapsed:.3f}s"


# ── Gate-red/green repro: scripts/setup_test_db.py CLI exit code 계약 ──


def test_setup_test_db_cli_rejects_invalid_name_nonzero_exit() -> None:
    """게이트 적색 재현: DB 이름이 규칙(소문자·숫자·밑줄 40자)을 벗어나면
    CLI가 0이 아닌 코드로 종료한다(`main()`의 `_NAME_RE` 가드, setup_test_db.py
    line ~210)."""
    argv = sys.argv
    sys.argv = ["setup_test_db.py", "Bad-Name!"]
    try:
        with pytest.raises(SystemExit) as exc_info:
            setup_test_db_cli.main()
    finally:
        sys.argv = argv
    assert exc_info.value.code not in (0, None)


def test_setup_test_db_cli_list_is_gate_green_real_db() -> None:
    """게이트 초록 재현: `--list`는 실 DB(read-only, `aios_test_%` 조회만)에
    접속해 0으로 종료한다 — 위 적색 재현과 짝을 이루는 성공 경로."""
    argv = sys.argv
    sys.argv = ["setup_test_db.py", "--list"]
    try:
        exit_code = setup_test_db_cli.main()
    finally:
        sys.argv = argv
    assert exit_code == 0
