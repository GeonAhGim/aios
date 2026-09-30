"""scripts/backup/restore_drill.py 단위 테스트 -- backup-tree 복사/tar 추출 경로.

Split out of `test_restore_drill.py` (ADR-2026-09-10-C §7 file policy — the source
file crossed the 800-line LOC observation threshold after task-9469's
`_tar_binary()` split grew it, task-9468 main_drift). No behavior change: same test
bodies, covering `_copy_backup_tree` (robocopy/shutil.copytree), `_extract_tar_backup`
(tar -Ft 포맷), `_restore_backup_files`의 포맷 분기.
"""

from __future__ import annotations

from pathlib import Path

from scripts.backup import restore_drill


def test_copy_backup_tree_non_windows_uses_shutil_copytree(tmp_path: Path, monkeypatch):
    """esc-health-backup_drill_failed: 110,830개 파일·2.4GB 백업을 shutil.copytree로
    옮기는 데 시간제한이 없어 nightly의 외부 1200s 하드킬에 걸렸다(steps={}). posix에서는
    여전히 shutil.copytree를 쓰지만(robocopy는 Windows 전용), 결과 계약은 동일하다."""
    monkeypatch.setattr(restore_drill.os, "name", "posix")
    src = tmp_path / "src"
    src.mkdir()
    (src / "PG_VERSION").write_text("16", encoding="utf-8")
    dst = tmp_path / "dst"

    ok, detail = restore_drill._copy_backup_tree(src, dst, 30.0)

    assert ok is True
    assert detail == ""
    assert (dst / "PG_VERSION").read_text(encoding="utf-8") == "16"


def test_copy_backup_tree_windows_uses_robocopy_and_succeeds_on_low_returncode(
    tmp_path: Path, monkeypatch
):
    """robocopy 종료코드 0-7은 성공(파일 복사/스킵 조합) -- 8 이상만 실패다."""
    monkeypatch.setattr(restore_drill.os, "name", "nt")

    class _FakeCompleted:
        returncode = 1  # robocopy: 파일이 복사됨

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        assert cmd[0] == "robocopy"
        return _FakeCompleted()

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, detail = restore_drill._copy_backup_tree(tmp_path / "src", tmp_path / "dst", 30.0)

    assert ok is True


def test_copy_backup_tree_windows_high_returncode_is_failure(tmp_path: Path, monkeypatch):
    """robocopy 종료코드 8 이상은 실패 -- 성공으로 오분류하면 손상된 복구본을 그대로
    기동 시도하게 된다."""
    monkeypatch.setattr(restore_drill.os, "name", "nt")

    class _FakeCompleted:
        returncode = 16  # robocopy: 심각한 오류(예: 소스 접근 실패)

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        return _FakeCompleted()

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, _detail = restore_drill._copy_backup_tree(tmp_path / "src", tmp_path / "dst", 30.0)

    assert ok is False


def test_copy_backup_tree_windows_timeout_returns_diagnostic_instead_of_hanging(
    tmp_path: Path, monkeypatch
):
    """esc-health-backup_drill_failed의 핵심 결함: 복사 단계에 시간제한이 없어 무엇이
    멈췄는지조차 관측 못 했다. 이제 timeout이 있으면 진단 가능한 실패로 끝난다."""
    monkeypatch.setattr(restore_drill.os, "name", "nt")

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        raise restore_drill.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, detail = restore_drill._copy_backup_tree(tmp_path / "src", tmp_path / "dst", 5.0)

    assert ok is False
    assert detail == "timeout 5s"


def test_extract_tar_backup_fails_when_tar_binary_missing(tmp_path: Path, monkeypatch):
    """esc-health-backup_drill_failed: tar가 PATH에 없는 환경에서는 추출 시도 전에
    진단 가능한 실패로 끝나야 한다(무기한 대기/모호한 스택트레이스 대신)."""
    monkeypatch.setattr(restore_drill, "_tar_binary", lambda: (None, []))

    ok, detail = restore_drill._extract_tar_backup(tmp_path / "src", tmp_path / "dst", 30.0)

    assert ok is False
    assert "tar" in detail


def test_extract_tar_backup_fails_when_base_tar_missing(tmp_path: Path, monkeypatch):
    """base.tar(.gz)가 없는 디렉터리(예: 이미 손상된 백업)를 조용히 빈 복구본으로
    넘기지 않고 즉시 실패시킨다."""
    monkeypatch.setattr(restore_drill, "_tar_binary", lambda: ("/usr/bin/tar", ["--force-local"]))
    src = tmp_path / "src"
    src.mkdir()

    ok, detail = restore_drill._extract_tar_backup(src, tmp_path / "dst", 30.0)

    assert ok is False
    assert "base.tar" in detail


def test_extract_tar_backup_extracts_base_and_wal_tar(tmp_path: Path, monkeypatch):
    """base.tar.gz + pg_wal.tar.gz 둘 다 있으면 각각 dst/, dst/pg_wal/로 풀린다
    (-Ft -z pg_basebackup 산출물 레이아웃과 일치해야 restore가 정상 기동한다)."""
    monkeypatch.setattr(restore_drill, "_tar_binary", lambda: ("/usr/bin/tar", ["--force-local"]))
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.tar.gz").write_bytes(b"fake-tar")
    (src / "pg_wal.tar.gz").write_bytes(b"fake-wal-tar")
    dst = tmp_path / "dst"

    calls = []

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        calls.append([*cmd, f"cwd={cwd}"])

        class _FakeCompleted:
            returncode = 0

        return _FakeCompleted()

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, detail = restore_drill._extract_tar_backup(src, dst, 30.0)

    assert ok is True
    assert detail == str(dst)
    assert len(calls) == 2
    # task-9469: -C 대신 cwd로 추출 위치를 넘긴다(드라이브 문자 인자를 tar가 원격 호스트로 오인)
    assert calls[0][:3] == ["/usr/bin/tar", "--force-local", "-xf"]
    assert str(src / "base.tar.gz") in calls[0]
    assert f"cwd={dst}" in calls[0]
    assert str(src / "pg_wal.tar.gz") in calls[1]
    assert f"cwd={dst / 'pg_wal'}" in calls[1]


def test_extract_tar_backup_reports_extraction_failure(tmp_path: Path, monkeypatch):
    """tar 추출이 0이 아닌 코드로 끝나면(손상된 아카이브 등) 실패로 보고하고 뒤 단계로
    넘어가지 않는다."""
    monkeypatch.setattr(restore_drill, "_tar_binary", lambda: ("/usr/bin/tar", ["--force-local"]))
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.tar").write_bytes(b"corrupt")

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        class _FakeCompleted:
            returncode = 2

        return _FakeCompleted()

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, detail = restore_drill._extract_tar_backup(src, tmp_path / "dst", 30.0)

    assert ok is False
    assert "rc=2" in detail


def test_extract_tar_backup_timeout_returns_diagnostic(tmp_path: Path, monkeypatch):
    """추출 단계도 복사 단계와 동일하게 timeout이 있어야 한다 -- 무기한 대기가
    esc-health-backup_drill_failed의 근본 실패 패턴이었다."""
    monkeypatch.setattr(restore_drill, "_tar_binary", lambda: ("/usr/bin/tar", ["--force-local"]))
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.tar.gz").write_bytes(b"fake-tar")

    def fake_run(cmd, stdout, stderr, timeout, check, cwd=None):
        raise restore_drill.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(restore_drill.subprocess, "run", fake_run)

    ok, detail = restore_drill._extract_tar_backup(src, tmp_path / "dst", 5.0)

    assert ok is False
    assert "timeout 5s" in detail


def test_restore_backup_files_dispatches_to_tar_extraction_when_base_tar_present(
    tmp_path: Path, monkeypatch
):
    """run_drill()이 새 -Ft(tar) 포맷 백업을 만나면 robocopy/copytree가 아니라
    tar 추출 경로로 간다(esc-health-backup_drill_failed 근본 수정: base_backup.py가
    -Fp에서 -Ft -z로 바뀌었으므로 복구 쪽도 같이 바뀌어야 한다)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "base.tar.gz").write_bytes(b"fake-tar")

    called = {}

    def fake_extract(s, d, t):
        called["args"] = (s, d, t)
        return True, str(d)

    monkeypatch.setattr(restore_drill, "_extract_tar_backup", fake_extract)

    ok, detail = restore_drill._restore_backup_files(src, tmp_path / "dst", 30.0)

    assert ok is True
    assert called["args"][0] == src


def test_restore_backup_files_falls_back_to_copytree_for_plain_format_backup(
    tmp_path: Path, monkeypatch
):
    """디스크에 남아있는 과거 -Fp(plain) 백업(기존 로직 대상)은 여전히
    _copy_backup_tree로 복구된다 -- tar 포맷으로 전환됐다고 예전 백업이 못 쓰게 되면
    안 된다(하위 호환)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "PG_VERSION").write_text("16", encoding="utf-8")

    called = {}

    def fake_copytree(s, d, t):
        called["args"] = (s, d, t)
        return True, str(d)

    monkeypatch.setattr(restore_drill, "_copy_backup_tree", fake_copytree)

    ok, detail = restore_drill._restore_backup_files(src, tmp_path / "dst", 30.0)

    assert ok is True
    assert called["args"][0] == src
