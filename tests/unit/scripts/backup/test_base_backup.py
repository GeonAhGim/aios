"""scripts/backup/base_backup.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

pg_basebackup 실행 파일 없이도(`which`/`run_cmd` 주입) 성공/실패 경로를 모두 검증한다.
DB·네트워크 접근 없음.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.backup import base_backup


def test_pg_conn_args_extracts_host_port_user():
    args = base_backup.pg_conn_args("postgresql://alice@db.internal:5555/aios_dev")
    assert args == ["-h", "db.internal", "-p", "5555", "-U", "alice"]


def test_pg_conn_args_defaults_host_and_port():
    args = base_backup.pg_conn_args("postgresql+asyncpg:///aios_dev")
    assert args == ["-h", "localhost", "-p", "5432"]


def test_run_base_backup_missing_binary_raises(tmp_path: Path):
    with pytest.raises(RuntimeError, match="pg_basebackup"):
        base_backup.run_base_backup(tmp_path, "postgresql://x/y", which=lambda _b: None)


def test_run_base_backup_success_writes_manifest(tmp_path: Path):
    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=lambda cmd, env, timeout: (0, "backup done"),
    )
    assert manifest["ok"] is True
    dest = Path(manifest["dest"])
    assert dest.is_dir()
    saved = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True
    assert saved["tail"] == "backup done"


def test_run_base_backup_failure_removes_dir_and_records_failed_marker(tmp_path: Path):
    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=lambda cmd, env, timeout: (1, "connection refused"),
    )
    assert manifest["ok"] is False
    dest = Path(manifest["dest"])
    assert not dest.exists()  # 실패작은 지운다 -- latest_backup_dir이 집어가면 안 된다
    failed_markers = list(tmp_path.glob("*-FAILED.json"))
    assert len(failed_markers) == 1
    saved = json.loads(failed_markers[0].read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert "connection refused" in saved["tail"]


def test_latest_backup_dir_returns_none_when_empty(tmp_path: Path):
    assert base_backup.latest_backup_dir(tmp_path) is None


def test_latest_backup_dir_skips_failed_and_picks_newest_ok(tmp_path: Path):
    older = tmp_path / "20260101T000000Z"
    older.mkdir()
    (older / "manifest.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    failed = tmp_path / "20260102T000000Z"
    failed.mkdir()
    (failed / "manifest.json").write_text(json.dumps({"ok": False}), encoding="utf-8")

    newest = tmp_path / "20260103T000000Z"
    newest.mkdir()
    (newest / "manifest.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    assert base_backup.latest_backup_dir(tmp_path) == newest


def test_latest_backup_dir_ignores_corrupt_manifest(tmp_path: Path):
    corrupt = tmp_path / "20260101T000000Z"
    corrupt.mkdir()
    (corrupt / "manifest.json").write_text("{not json", encoding="utf-8")

    assert base_backup.latest_backup_dir(tmp_path) is None


def test_run_base_backup_dest_write_failure_cleans_up(tmp_path: Path):
    """run_cmd가 OSError를 던질 때 run_base_backup이 FAILED 마커를 남기고 dest를 정리하는지 검증."""
    # run_cmd가 OSError를 던지면: target 디렉터리는 이미 생성됨 → cleanup 필요
    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=lambda cmd, env, timeout: (_ for _ in ()).throw(OSError("disk full", "/dev/sda1")),
    )
    assert manifest["ok"] is False
    # target 디렉터리는 rmtree로 정리됨
    assert not Path(manifest["dest"]).exists()
    # FAILED marker가 남음
    failed_markers = list(tmp_path.glob("*-FAILED.json"))
    assert len(failed_markers) == 1
    saved = json.loads(failed_markers[0].read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert "disk full" in saved["tail"]


def test_run_base_backup_timeout_error_leaves_failed_marker(tmp_path: Path):
    """run_cmd가 TimeoutExpired를 던질 때 FAILED 마커를 남기고 dest를 정리하는지 검증."""
    import subprocess

    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=lambda cmd, env, timeout: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd, timeout)
        ),
    )
    assert manifest["ok"] is False
    assert manifest["returncode"] == 124
    assert not Path(manifest["dest"]).exists()
    failed_markers = list(tmp_path.glob("*-FAILED.json"))
    assert len(failed_markers) == 1
    saved = json.loads(failed_markers[0].read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert "timeout" in saved["tail"]


def test_pg_basebackup_command_includes_wal_method_stream_and_replication_slot(
    tmp_path: Path,
) -> None:
    """pg_basebackup 호출에 --wal-method=stream 과 -C -S aios_drill 이 반드시 포함됨을 확인한다.

    WAL 소실(requested WAL already removed) 오류를 방지하기 위해 stream 모드로
    WAL 을 백업 중 실시간 전송하고, 복제 슬롯 aios_drill 로 WAL 보존을 보장한다
    (task-4940).
    """
    captured: list[list[str]] = []

    def run_cmd(cmd: list[str], env: dict | None, timeout: int) -> tuple[int, str]:
        captured.append(cmd)
        return (0, "backup done")

    base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=run_cmd,
    )

    basebackup_cmds = [c for c in captured if c[0] == "pg_basebackup"]
    assert len(basebackup_cmds) == 1
    cmd = basebackup_cmds[0]
    assert "--wal-method=stream" in cmd, "--wal-method=stream 이 없다 — WAL 소실 재발"
    assert "--checkpoint=fast" in cmd, "--checkpoint=fast 없음 — 체크포인트 대기 중 WAL 재활용"
    assert "-C" in cmd, "-C (replication slot 생성) 이 없다"
    assert "-S" in cmd, "-S 가 없다"
    assert "aios_drill" in cmd, "슬롯 이름 aios_drill 이 없다"


def test_stale_slot_is_dropped_before_pg_basebackup(tmp_path: Path) -> None:
    """드릴 중단으로 남은 aios_drill 슬롯은 pg_basebackup -C 전에 제거돼야 한다.

    제거하지 않으면 -C 가 already exists 로 실패한다.
    """
    captured: list[list[str]] = []

    def run_cmd(cmd: list[str], env: dict | None, timeout: int) -> tuple[int, str]:
        captured.append(cmd)
        return (0, "ok")

    base_backup.run_base_backup(
        tmp_path,
        "postgresql+asyncpg://u@db:5432/aios",
        which=lambda b: b,
        run_cmd=run_cmd,
    )

    names = [c[0] for c in captured]
    assert names == ["psql", "pg_basebackup"]
    drop_cmd = captured[0]
    assert "postgresql://u@db:5432/aios" in drop_cmd
    assert "pg_drop_replication_slot('aios_drill')" in drop_cmd[-1]


def test_failed_slot_drop_does_not_block_backup(tmp_path: Path) -> None:
    """실패 주입: 슬롯 정리 명령이 OSError를 내도 백업은 계속 진행하고 manifest가 기록된다."""
    calls: list[str] = []

    def run_cmd(cmd: list[str], env: dict | None, timeout: int) -> tuple[int, str]:
        calls.append(cmd[0])
        if cmd[0] == "psql":
            raise OSError("psql crashed")
        return (0, "backup done")

    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda b: b,
        run_cmd=run_cmd,
    )

    assert calls == ["psql", "pg_basebackup"]
    assert manifest["ok"] is True


def test_run_base_backup_perf_within_budget(perf_budget, tmp_path: Path) -> None:
    """성능 단언: run_base_backup(주입된 run_cmd/which)가 1회 호출당 wall 50ms 이내여야 한다.

    실제 pg_basebackup 을 호출하지 않으므로 이 수치는 파일 I/O + manifest 기록 + 슬롯 DROP
    확인의 순수 Python 오버헤드 기준이다. ADR-2026-09-09-C Decision 1 — 백업 스크립트
    호출 오버헤드는 사전거래 게이트(5ms)보다 느려도 되지만 50ms 를 넘지 않아야 CI 병렬
    실행에서 병목이 되지 않는다.
    """

    import uuid

    def _one_call() -> dict:
        run_dir = tmp_path / f"perf_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return base_backup.run_base_backup(
            run_dir,
            "postgresql://x/y",
            which=lambda _b: "/usr/bin/pg_basebackup",
            run_cmd=lambda cmd, env, timeout: (0, "ok"),
        )

    perf_budget.assert_within(
        _one_call,
        budget_ms=50.0,
        n=5,
        warmup=1,
        label="run_base_backup",
    )


def test_wal_already_removed_gate_red_reproduction(tmp_path: Path) -> None:
    """게이트 적색 재현: pg_basebackup 이 'requested WAL already removed' 로 실패하면
    manifest.ok=False 로 기록되고 FAILED 마커가 남는다.

    원본 이슈(task-4940): pg_basebackup 이 --wal-method=stream 없이 실행될 때
    WAL 세그먼트가 회수되어 "requested WAL segment ... has already been removed"
    오류가 발생했다. 이 테스트는 실패 시 manifest 가 정확히 기록되는지 확인하고,
    fix(--wal-method=stream + 복제 슬롯) 이 적용된 후에도 실패 경로가 여전히
    올바른 manifest 로 기록되는지 검증한다.
    """

    manifest = base_backup.run_base_backup(
        tmp_path,
        "postgresql://x/y",
        which=lambda _b: "/usr/bin/pg_basebackup",
        run_cmd=lambda cmd, env, timeout: (
            1,
            "pg_basebackup: error: could not send replication data: "
            "requested WAL segment 00000001000000060000000B has already been removed",
        ),
    )

    assert manifest["ok"] is False
    assert manifest["returncode"] == 1
    assert "already been removed" in manifest["tail"]
    assert not Path(manifest["dest"]).exists()  # 실패 시 디렉터리 정리됨
    failed_markers = list(tmp_path.glob("*-FAILED.json"))
    assert len(failed_markers) == 1
    saved = json.loads(failed_markers[0].read_text(encoding="utf-8"))
    assert saved["ok"] is False


def test_drop_stale_slot_passes_password_via_env_not_argv(tmp_path: Path) -> None:
    """_drop_stale_slot이 DSN 비밀번호를 argv가 아닌 PGPASSWORD env로 전달하는지 검증.

    task-21543: psql <dsn> 형태로 argv에 비밀번호가 노출되면 ps 목록에 드러난다.
    """
    calls: list[tuple[list[str], dict | None]] = []

    def mock_run(cmd, env, timeout):
        calls.append((list(cmd), env))
        return (0, "")

    base_backup.run_base_backup(
        tmp_path,
        "postgresql://user:secret123@db:5432/aios",
        which=lambda b: "/usr/bin/pg_basebackup" if "pg_basebackup" in b else "/usr/bin/psql",
        run_cmd=mock_run,
    )
    # psql 호출 (첫 번째) 에서 비밀번호가 argv에 없어야 함
    psql_calls = [c for c in calls if "psql" in c[0][0]]
    assert len(psql_calls) == 1
    psql_argv, psql_env = psql_calls[0]
    assert "secret123" not in psql_argv
    assert psql_env is not None
    assert psql_env.get("PGPASSWORD") == "secret123"
