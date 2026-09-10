"""H-4(task-2609, ADR-2026-09-09-B) -- pg_basebackup 래퍼: 물리 베이스 백업 1회 생성.

PITR(point-in-time recovery)은 "베이스 백업 + 그 이후 연속된 WAL"이 있어야 성립한다.
이 스크립트는 그중 베이스 백업 절반만 맡는다(WAL 연속성 검증은 wal_archive.py,
복구 자체는 restore_drill.py -- 세 스크립트가 파일당 한 책임을 나눠 가진다).

실행은 저장소 루트에서 모듈 형태로: `python -m scripts.backup.base_backup --dest-dir ...`
(직접 실행 `python scripts/backup/base_backup.py`는 다른 scripts/ 스크립트와 같은 이유로
`src...` 임포트가 필요해지면 깨진다 -- 이 스크립트는 아직 그럴 일이 없지만 규약을 맞춘다).

DATABASE_URL(환경변수 -> .env)에서 접속정보를 얻는다(scripts/setup_test_db.py와 동일 규약).
매 실행마다 <dest-dir>/<UTC 타임스탬프>/ 아래 `pg_basebackup -Fp -Xs -P`를 실행하고, 같은
디렉터리에 manifest.json(시작/종료 시각, ok, 라벨, stderr tail)을 남긴다. 실패하면 그
디렉터리는 지우고(다음 restore_drill이 실패작을 최신 백업으로 오인해 집어가지 않도록)
<dest-dir>/<타임스탬프>-FAILED.json에 실패 사실만 남긴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def server_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL이 환경변수 또는 .env에 없습니다.")
    return url


def pg_conn_args(url: str) -> list[str]:
    """postgresql[+asyncpg]://user:pass@host:port/db -> pg_basebackup -h/-p/-U 인자."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    args = ["-h", parts.hostname or "localhost", "-p", str(parts.port or 5432)]
    if parts.username:
        args += ["-U", parts.username]
    return args


def _run(cmd: list[str], env: dict | None, timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout}s"


def run_base_backup(
    dest_dir: Path,
    dsn: str,
    *,
    label: str | None = None,
    pg_basebackup_bin: str = "pg_basebackup",
    run_cmd: Callable[[list[str], dict | None, int], tuple[int, str]] = _run,
    which: Callable[[str], str | None] = shutil.which,
    timeout: int = 3600,
) -> dict:
    """베이스 백업 1회 실행 + manifest 기록. `run_cmd`/`which`는 실제 pg_basebackup 없이
    성공/실패 경로를 검증하기 위한 주입 지점이다(H-4 DoD: 실패 주입 테스트)."""
    if which(pg_basebackup_bin) is None:
        raise RuntimeError(f"{pg_basebackup_bin} 실행 파일을 찾을 수 없습니다(PATH 확인).")

    started = _now()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    target = dest_dir / stamp
    target.mkdir(parents=True, exist_ok=False)
    backup_label = label or f"aios-base-{stamp}"
    cmd = [pg_basebackup_bin, *pg_conn_args(dsn), "-D", str(target),
           "-Fp", "-Xs", "-P", "-l", backup_label]
    env = {**os.environ}
    rc, tail = run_cmd(cmd, env, timeout)
    finished = _now()

    manifest = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "ok": rc == 0,
        "returncode": rc,
        "dest": str(target),
        "label": backup_label,
        "tail": tail,
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    if rc == 0:
        (target / "manifest.json").write_text(payload, encoding="utf-8")
    else:
        shutil.rmtree(target, ignore_errors=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / f"{stamp}-FAILED.json").write_text(payload, encoding="utf-8")
    return manifest


def latest_backup_dir(dest_dir: Path) -> Path | None:
    """ok=true manifest.json이 있는 가장 최근(타임스탬프 이름 내림차순) 백업 디렉터리."""
    if not dest_dir.exists():
        return None
    candidates = sorted(
        (p for p in dest_dir.iterdir() if p.is_dir() and (p / "manifest.json").is_file()),
        key=lambda p: p.name, reverse=True,
    )
    for p in candidates:
        try:
            manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("ok"):
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest-dir", required=True, help="백업을 쌓을 상위 디렉터리")
    parser.add_argument("--dsn", help="생략하면 DATABASE_URL(.env)을 쓴다")
    parser.add_argument("--label", help="pg_basebackup -l 라벨")
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dsn = args.dsn or server_url()
    try:
        manifest = run_base_backup(dest_dir, dsn, label=args.label)
    except RuntimeError as e:
        print(f"base_backup 실패: {e}", file=sys.stderr)
        return 1
    if not manifest["ok"]:
        print(f"pg_basebackup 실패(rc={manifest['returncode']}): {manifest['tail']}",
              file=sys.stderr)
        return 1
    print(f"base backup 완료: {manifest['dest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
