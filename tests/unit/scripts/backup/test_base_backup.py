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
        tmp_path, "postgresql://x/y",
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
        tmp_path, "postgresql://x/y",
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
