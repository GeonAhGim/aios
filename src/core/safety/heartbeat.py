"""메인 프로세스 heartbeat — 9.1/9.3이 공유하는 IPC 메커니즘.

Spec: 기능설계문서_v1.20.md#FD-9.1 ("메인 프로세스 heartbeat(별도 IPC/
파일 타임스탬프)")

Watchdog은 메인 프로세스와 완전히 격리된 별도 프로세스로 동작해야 하므로
(정책문서 8.6-A), 공유 메모리가 아니라 파일 타임스탬프로 통신한다 — 가장
단순하고 프로세스 경계를 명확히 넘는 방식(과잉설계 방지, 17.9-A).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# main.py(메인 프로세스)와 watchdog_process.py(별도 프로세스)가 공유하는
# 유일한 통신 경로 — 프로세스 메모리 경계를 파일 하나로 명확히 넘는다.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HEARTBEAT_PATH = _PROJECT_ROOT / "runtime" / "main_process.heartbeat"


def write_heartbeat(path: Path) -> None:
    """메인 프로세스가 주기적으로 호출 — 파일에 현재 시각을 기록한다.

    레드팀 감사(docs/RED_TEAM_FINDINGS.md #07) 반영 — 대상 파일에 직접
    write_text()하면 Watchdog이 정확히 그 truncate~쓰기 사이 찰나에
    같은 파일을 읽을 경우 손상된 값을 보고 거짓 HALT를 유발할 수 있다.
    임시파일에 먼저 쓰고 os.replace()로 교체한다 — POSIX/Windows NTFS
    모두 os.replace는 원자적이라 읽는 쪽은 항상 "이전 값 전체" 또는
    "새 값 전체" 중 하나만 본다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(str(time.time()), encoding="utf-8")
    os.replace(tmp_path, path)


def read_heartbeat_age_seconds(path: Path) -> float:
    """파일이 없거나 손상된 경우 무한대로 간주한다(응답 없음 = 낙관적으로
    보지 않는다, 9.1/9.3 공통 원칙)."""
    if not path.exists():
        return float("inf")
    try:
        last = float(path.read_text(encoding="utf-8"))
    except ValueError:
        return float("inf")
    return time.time() - last
