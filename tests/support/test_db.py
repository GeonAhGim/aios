"""PLT-36 tests/support/db.py — negative/failure-injection tests.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.4/§9 PLT-36.
DoD: negative test ≥3, failure-injection ≥1, perf assertion, gate-red repro.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import asyncpg
import pytest

from tests.support.db import (
    _asyncpg_dsn,
    _db_name,
    _with_database,
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
