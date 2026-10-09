"""restore_drill.py 복구 리허설용 DSN 변환 + recovery 설정 파일 생성.

ADR-2026-09-10-C 파일 정책에 따라 restore_drill.py(627줄, 500줄 경고)에서 분리됐다 --
"어느 DSN/포트로 붙을지"와 "어떤 recovery.signal/postgresql.auto.conf를 심을지"는 복구
실행(run_drill의 기동/대기/정리 오케스트레이션)과 분리된 책임이다. 공개 이름(libpq_dsn,
with_port, write_recovery_config)은 restore_drill.py가 그대로 재노출해 호출부가 바뀌지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def libpq_dsn(url: str) -> str:
    """SQLAlchemy 스킴(postgresql+asyncpg://)을 psql/libpq가 파싱하는 postgresql://로."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def with_port(url: str, port: int) -> str:
    """접속 URL의 host는 유지하고 port만 별도 인스턴스 것으로 바꾼다."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    userinfo, _, hostport = parts.netloc.rpartition("@")
    host = parts.hostname or "127.0.0.1"
    new_netloc = f"{userinfo}@{host}:{port}" if userinfo else f"{host}:{port}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


def _escape_path_for_pg_conf(path: Path) -> str:
    """PostgreSQL postgresql.conf 값에 넣기 전에 Windows 경로를 forward slash로 변환한다.

    PostgreSQL GUC 문자열 파서는 따옴표로 감싼 값 안의 백슬래시 시퀀스를
    C-스타일 escape로 해석한다(\\b -> backspace, \\a -> bell 등).
    Windows 경로에 역슬래시가 섞이면 restore_command 등에 치명적인 corruption을
    일으키므로, conf 파일에 쓰기 전에 모든 역슬래시를 forward slash로 통일한다.
    PostgreSQL 문서도 Windows 경로는 conf 파일에 forward slash로 쓰라고 권장한다.
    """
    return str(path).replace("\\", "/")


def write_recovery_config(data_dir: Path, archive_dir: Path) -> None:
    (data_dir / "recovery.signal").touch()
    # backslash -> forward slash (GUC escape corruption 방지)
    safe_archive = _escape_path_for_pg_conf(archive_dir)
    # Windows(cp 명령어 미존재)는 copy, Unix는 cp 사용
    restore_cmd = "copy" if os.name == "nt" else "cp"
    restore_command = f'{restore_cmd} "{safe_archive}/%f" "%p"'
    conf = data_dir / "postgresql.auto.conf"
    existing = conf.read_text(encoding="utf-8") if conf.exists() else ""
    # CTO 2026-09-30(task-9469): 리허설 인스턴스는 버리는 사본이다.
    # - archive_mode=off: 원본의 archive_command를 물려받아 운영 WAL 아카이브에 자기 타임라인
    #   기록(00000002.history)을 써 넣던 결함 차단 — 남으면 이후 복구가 엉뚱한 타임라인을 쫓는다.
    # - recovery_target_timeline=current: 아카이브에 다른 타임라인 기록이 있어도
    #   백업의 타임라인만 재생한다.
    # - fsync=off: 기동 시 데이터 디렉터리 전체 fsync(파일 10만 개 기준 수 분, 09-23 로그 실측)를
    #   건너뛴다. 사본의 내구성은 검증 대상이 아니다(복구 가능성·재생 일치가 대상).
    scratch = (
        f"\nrestore_command = '{restore_command}'\n"
        "archive_mode = off\n"
        "recovery_target_timeline = 'current'\n"
        "fsync = off\n"
    )
    conf.write_text(existing + scratch, encoding="utf-8")
