"""Backup restore helpers: copy / tar-extract backup tree to a restore data directory.

Imported by restore_drill.py so the main file stays under 500 lines.
Public API: _restore_backup_files(src, dst, timeout) -> (ok: bool, detail: str).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _copy_backup_tree(src: Path, dst: Path, timeout: float) -> tuple[bool, str]:
    """esc-health-backup_drill_failed: 시간제한 없는 shutil.copytree가 110,830개
    파일·2.4GB 백업(실측)을 옮기다 nightly 외부 하드킬(1200s)에 걸려 steps={}로만
    관측됐다. Windows는 robocopy /MT(다중 I/O 스레드)+자체 timeout으로 대체 --
    끝내 느려도 무한정 먹통이 아니라 진단 가능한 실패로 끝난다."""
    if os.name == "nt":
        import tempfile

        cmd = [
            "robocopy",
            str(src),
            str(dst),
            "/E",
            "/MT:32",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
            "/R:1",
            "/W:1",
        ]
        try:
            with tempfile.TemporaryFile(mode="w+b") as out:
                try:
                    r = subprocess.run(
                        cmd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout, check=False
                    )
                except subprocess.TimeoutExpired:
                    return False, f"timeout {timeout:.0f}s"
                out.seek(0)
                text = out.read().decode("utf-8", errors="replace")
            return r.returncode < 8, text[-4000:]  # robocopy: 0-7 성공, 8+ 실패
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
    try:
        shutil.copytree(src, dst)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _tar_binary() -> tuple[str | None, list[str]]:
    """(tar 경로, 드라이브 문자 경로용 추가 인자). Windows는 시스템 bsdtar를 우선한다 — PATH 순서
    (Git Bash에서 띄우면 MSYS GNU tar)에 따라 결과가 달라지지 않게 한다.
    GNU tar만 --force-local이 필요하다."""
    sys_tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if os.name == "nt" and sys_tar.exists():
        return str(sys_tar), []
    found = shutil.which("tar")
    return found, (["--force-local"] if found else [])


def _extract_tar_backup(src: Path, dst: Path, timeout: float) -> tuple[bool, str]:
    r"""esc-health-backup_drill_failed 근본 원인(2026-09-29 조사): 이 클러스터가 쌓은 고아
    aios_test_* DB 474개가 base/ 아래 오브젝트 디렉터리당 파일 ~1,000개씩, 총 47만개+를
    만들어 -Fp(파일별 복사) pg_basebackup 자체가 nightly 1200s 예산을 다 태웠다(steps={}로
    아무 단계도 못 남기고 죽음 -- copy 단계 이전, pg_basebackup 자체가 병목). base_backup.py를
    -Ft(+gzip)로 바꿔 base.tar(.gz) 1~2개 파일로 접었으니, 여기서는 그걸 풀기만 한다(진짜
    "불필요 데이터 정리"가 아니라 파일 수 자체를 줄이는 정정 -- 타임아웃 상향 금지 B-2).
    task-9469(2026-09-30): 이 PC의 PATH에 걸리는 tar가 Git for Windows의 MSYS GNU tar라,
    드라이브 문자 뒤 콜론이 있는 인자를 rmt(원격 테이프) host:path 문법으로 오인한다.
    `-f <경로>`는 `--force-local`을 붙이면 이 오인을 피하지만, 같은 tar 빌드에서
    `-C <드라이브문자:\...>`는 --force-local을 붙여도 그 인자를 또 다른 archive로
    오인해 "Cannot open"으로 죽는다(실측 확인) -- 그래서 -C 자체를 아예 안 쓰고
    subprocess의 cwd로 추출 대상 디렉터리를 바꿔 넘긴다(드라이브 문자가 인자 문자열에
    안 섞이므로 두 문제 모두 피한다). base.tar 추출이 한 번도 성공한 적이 없던 이유였다."""
    tar_bin, local = _tar_binary()
    if tar_bin is None:
        return False, "tar 실행 파일을 찾을 수 없다(Windows 10+/bsdtar 또는 GNU tar 필요)"
    base_tar = next((p for p in (src / "base.tar.gz", src / "base.tar") if p.exists()), None)
    if base_tar is None:
        return False, f"{src}에 base.tar(.gz)가 없다"

    import tempfile

    def _run_tar(cmd: list[str], cwd: Path) -> tuple[int, str]:
        try:
            with tempfile.TemporaryFile(mode="w+b") as out:
                try:
                    r = subprocess.run(
                        cmd,
                        cwd=cwd,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        timeout=timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    return 124, f"timeout {timeout:.0f}s"
                out.seek(0)
                return r.returncode, out.read().decode("utf-8", errors="replace")[-2000:]
        except OSError as exc:
            return 1, f"{type(exc).__name__}: {exc}"

    dst.mkdir(parents=True, exist_ok=True)
    rc, tail = _run_tar([tar_bin, *local, "-xf", str(base_tar)], cwd=dst)
    if rc != 0:
        return False, f"base.tar 추출 실패(rc={rc}): {tail}"

    wal_tar = next((p for p in (src / "pg_wal.tar.gz", src / "pg_wal.tar") if p.exists()), None)
    if wal_tar is not None:
        wal_dir = dst / "pg_wal"
        wal_dir.mkdir(parents=True, exist_ok=True)
        rc, tail = _run_tar([tar_bin, *local, "-xf", str(wal_tar)], cwd=wal_dir)
        if rc != 0:
            return False, f"pg_wal.tar 추출 실패(rc={rc}): {tail}"

    return True, str(dst)


def _restore_backup_files(src: Path, dst: Path, timeout: float) -> tuple[bool, str]:
    """pg_basebackup 산출물이 tar 포맷(-Ft, base.tar[.gz])이면 추출하고, 예전 plain 포맷
    (-Fp, 디렉터리 통째)이면 기존 robocopy/copytree 경로를 그대로 쓴다 -- 디스크에 남아있는
    과거 plain 백업과도 호환된다."""
    if (src / "base.tar.gz").exists() or (src / "base.tar").exists():
        return _extract_tar_backup(src, dst, timeout)
    return _copy_backup_tree(src, dst, timeout)
