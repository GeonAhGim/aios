"""task-6337(OPS: test_db_bloat 재발 방지): nightly 잡이 `aios_test_*` 잔존 DB를
정리하려면 `pm/scripts/cleanup_orphan_test_dbs.py`가 이 저장소의
``scripts/setup_test_db.py --list`` 출력을 파싱할 수 있어야 한다(task-5807/5844 —
50개, 0.78GB 잔존분이 발견 당시 아무도 감지하지 못했던 근본 원인은 이 출력
계약이 검증되지 않았던 것). 정리 스크립트 본체와 nightly 잡 배선은 fleet
코드(`C:\\aios\\pm`)에 있고 워커는 그 저장소를 직접 고치지 않는다(CLAUDE.md §4) —
이 파일은 이 저장소가 소유한 쪽, 즉 pm이 서브프로세스로 소비하는
`--list` 출력 계약이 깨지지 않았음을 지킨다.

pm 쪽 파서(``cleanup_orphan_test_dbs.list_test_databases``)의 실제 로직을 그대로
미러링한다: 공백으로 정확히 2필드, 첫 필드는 ``aios_test_`` 접두어, 두 번째
필드는 정수 — 그 외 줄은 조용히 건너뛴다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
LIST_SUBPROCESS_BUDGET_S = 20.0


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_test_db = _load_module("setup_test_db_nightly", SCRIPTS_DIR / "setup_test_db.py")


def _parse_list_output(stdout: str) -> list[tuple[str, int]]:
    """pm/scripts/cleanup_orphan_test_dbs.py::list_test_databases의 파싱 규칙을
    그대로 미러링한다 — 두 저장소 사이의 계약이므로 pm 코드를 직접 임포트하지
    않고 이 파일이 그 규약을 스스로 지킨다."""
    rows: list[tuple[str, int]] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].startswith("aios_test_"):
            continue
        try:
            rows.append((parts[0], int(parts[1])))
        except ValueError:
            continue
    return rows


def _server_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture
def scratch_db_name() -> str:
    return f"aios_test_nightlyclean_{uuid4().hex[:12]}"


async def _drop_if_exists(server_url: str, database: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(
        setup_test_db._asyncpg_dsn(setup_test_db._with_database(server_url, "postgres"))
    )
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await admin.close()


# ---------------------------------------------------------------------------
# Positive: real DB round-trip through the subprocess boundary pm actually uses
# ---------------------------------------------------------------------------


def test_list_subprocess_output_is_parseable_and_includes_scratch_db(
    scratch_db_name: str,
) -> None:
    server_url = _server_url()
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    try:
        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "setup_test_db.py"), "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LIST_SUBPROCESS_BUDGET_S,
            check=True,
        )
        elapsed = time.monotonic() - start

        rows = _parse_list_output(result.stdout)
        names = {name for name, _size in rows}
        assert scratch_db_name in names
        assert all(name.startswith(setup_test_db.PREFIX) for name in names)
        assert all(size >= 0 for _name, size in rows)
        assert elapsed < LIST_SUBPROCESS_BUDGET_S, (
            f"--list subprocess took {elapsed:.2f}s, budget {LIST_SUBPROCESS_BUDGET_S}s "
            "(task-6096: bloat 상태에서 목록화 자체가 timeout에 걸리면 정리를 시작조차 못한다)"
        )
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_list_subprocess_surfaces_multiple_orphan_candidates(
    scratch_db_name: str,
) -> None:
    """red-gate repro: task-5807 사고(잔존 DB 50개가 아무 도구에도 안 잡혀 누적)를
    축소 재현한다 — 여러 개를 만들면 전부 한 번에 나열돼야 정리 대상 판별이
    가능하다."""
    server_url = _server_url()
    second_name = f"{scratch_db_name}_2"
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    asyncio.run(setup_test_db._ensure_database(server_url, second_name, reset=False))
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "setup_test_db.py"), "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LIST_SUBPROCESS_BUDGET_S,
            check=True,
        )
        names = {name for name, _size in _parse_list_output(result.stdout)}
        assert {scratch_db_name, second_name} <= names
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))
        asyncio.run(_drop_if_exists(server_url, second_name))


# ---------------------------------------------------------------------------
# Negative: malformed lines must not poison the orphan set (fail-closed on
# unparseable rows, never guess a name/size pm would then try to DROP)
# ---------------------------------------------------------------------------


def test_parse_skips_line_with_missing_size_field() -> None:
    stdout = "aios_test_pm\naios_test_qa 12345\n"
    rows = _parse_list_output(stdout)
    assert rows == [("aios_test_qa", 12345)]


def test_parse_skips_line_with_non_integer_size() -> None:
    stdout = "aios_test_pm not_a_number\naios_test_qa 12345\n"
    rows = _parse_list_output(stdout)
    assert rows == [("aios_test_qa", 12345)]


def test_parse_skips_line_without_prefix() -> None:
    """접두어 없는 줄(예: 로그 잡음, 다른 DB 이름)을 정리 대상으로 오인하면
    운영 DB를 건드릴 위험이 있다 — 조용히 건너뛴다."""
    stdout = "aios_dev 999999\naios_test_qa 12345\n"
    rows = _parse_list_output(stdout)
    assert rows == [("aios_test_qa", 12345)]


def test_parse_empty_output_yields_no_rows() -> None:
    assert _parse_list_output("") == []


# ---------------------------------------------------------------------------
# Failure injection: broken DATABASE_URL must fail loudly, not silently
# report an empty/partial list that pm would treat as "nothing to clean"
# ---------------------------------------------------------------------------


def test_list_subprocess_fails_closed_on_unreachable_server() -> None:
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://user:password@localhost:1/nope"}
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "setup_test_db.py"), "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LIST_SUBPROCESS_BUDGET_S,
            env=env,
            check=True,
        )
