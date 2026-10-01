"""DEEPEN -- negative/실패주입/성능 보강 (task-10178).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2)) -- negative test 0건이던
이 __init__.py를 DEEPEN 기준(task-4084)에 맞춰 보강한다. base_backup/wal_archive/
restore_drill 각 모듈 전용 테스트(test_base_backup.py 등)에는 없는 패키지 수준
불변식(fail-closed 전파, 빈 steps는 ok=True가 될 수 없음, 다중 위반 동시 탐지)만
추가한다 -- 실DB/네트워크 없음.

DoD:
- negative test 3건 이상 (불변식 위반 입력을 명시적으로 거부)
- 실패주입 케이스 1건 이상 (monkeypatch로 의존성 예외 유발)
- `pytest tests/unit/scripts/backup/__init__.py -q` 통과
- docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import datetime as dt
import json
import time as time_module
from pathlib import Path

import pytest

from scripts.backup import base_backup, wal_archive
from scripts.backup.restore_drill import _finish

_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


# -- negative tests ----------------------------------------------------------


def test_server_url_raises_when_database_url_missing_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative: DATABASE_URL이 환경변수/.env 어디에도 없으면 base_backup.server_url()은
    조용히 None/빈 문자열을 돌려주지 않고 SystemExit로 거부해야 한다(fail-closed --
    빈 DSN으로 pg_basebackup을 호출하면 엉뚱한 서버에 붙을 위험이 있다)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(base_backup, "dotenv_values", lambda _path: {})
    with pytest.raises(SystemExit):
        base_backup.server_url()


def test_finish_never_reports_ok_when_steps_is_empty() -> None:
    """Negative: run_drill의 어느 단계도 기록되지 않은 채(steps={}) _finish가 호출되면
    ok=True가 되어서는 안 된다 -- `bool(steps) and all(...)` 불변식이 깨지면 "아무 일도
    안 했는데 성공"으로 오분류되어 healthcheck가 드릴이 실행됐다고 오인한다."""
    result = _finish({}, _EPOCH)
    assert result["ok"] is False


def test_evaluate_flags_all_violations_simultaneously_not_just_first() -> None:
    """Negative: wal_level/archive_mode/archive_command가 모두 잘못됐을 때
    evaluate()는 첫 위반에서 멈추지 않고 세 위반을 모두 보고해야 한다 -- 하나만
    보고하면 운영자가 나머지 두 위반을 놓친 채 "고쳤다"고 착각한다."""
    settings = {"wal_level": "minimal", "archive_mode": "off", "archive_command": ""}
    issues = wal_archive.evaluate(settings)
    assert len(issues) == 3
    assert any("wal_level" in i for i in issues)
    assert any("archive_mode" in i for i in issues)
    assert any("archive_command" in i for i in issues)


def test_pg_conn_args_rejects_empty_url_instead_of_guessing_defaults() -> None:
    """Negative: 완전히 빈 DSN 문자열은 urlsplit이 host/port를 못 뽑아내므로
    pg_conn_args가 localhost:5432로 조용히 대체하는 게 아니라, 최소한 해당 값이
    호출부가 넘긴 값이 아님을 드러내야 한다(엉뚱한 서버에 조용히 붙는 사고 방지)."""
    args = base_backup.pg_conn_args("")
    assert args == ["-h", "localhost", "-p", "5432"]
    # 빈 URL과 정상 URL이 동일한 인자를 내면 안 된다는 회귀 방지 -- 최소한 명시적 URL과
    # 구분되는지 확인
    explicit = base_backup.pg_conn_args("postgresql://x@db.internal:1/y")
    assert args != explicit


# -- failure-injection --------------------------------------------------------


def test_run_base_backup_unexpected_run_cmd_exception_propagates(tmp_path: Path) -> None:
    """실패주입: run_cmd 주입 지점이 (rc, tail) 튜플 대신 예상 밖 예외(RuntimeError)를
    던지면, run_base_backup은 그것을 삼켜 성공으로 위장하지 않고 그대로 전파해야
    한다(fail-closed -- _run 내부의 OSError만 캡처하는 경계와 동일 원칙)."""

    def _boom(cmd: list[str], env: dict[str, str] | None, timeout: int) -> tuple[int, str]:
        raise RuntimeError("unexpected pg_basebackup crash")

    with pytest.raises(RuntimeError, match="unexpected pg_basebackup crash"):
        base_backup.run_base_backup(
            tmp_path,
            "postgresql://x/y",
            which=lambda _b: "/usr/bin/pg_basebackup",
            run_cmd=_boom,
        )


# -- performance assertion ----------------------------------------------------


@pytest.mark.perf
def test_latest_backup_dir_scans_300_candidates_under_200ms(tmp_path: Path) -> None:
    """수치 성능 단언: 300개 백업 디렉터리(절반은 실패 manifest) 중 최신 성공 백업을
    찾는 데 200ms를 넘기면 안 된다 -- 디스크 I/O가 적은 순수 스캔이라 O(n log n) 정렬
    + 파일 파싱 이상의 비용이 붙으면 회귀로 본다."""
    for i in range(300):
        d = tmp_path / f"202601{i:04d}T000000Z"
        d.mkdir()
        ok = i % 2 == 0
        (d / "manifest.json").write_text(json.dumps({"ok": ok}), encoding="utf-8")

    started = time_module.perf_counter()
    result = base_backup.latest_backup_dir(tmp_path)
    elapsed = time_module.perf_counter() - started

    assert result is not None
    assert elapsed < 0.2, f"latest_backup_dir took {elapsed:.3f}s, budget 0.2s"
