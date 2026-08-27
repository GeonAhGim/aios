"""메인 프로세스 heartbeat — 9.1/9.3이 공유하는 IPC 메커니즘.

Spec: 기능설계문서_v1.20.md#FD-9.1 ("메인 프로세스 heartbeat(별도 IPC/
파일 타임스탬프)")

Watchdog은 메인 프로세스와 완전히 격리된 별도 프로세스로 동작해야 하므로
(정책문서 8.6-A), 공유 메모리가 아니라 파일 타임스탬프로 통신한다 — 가장
단순하고 프로세스 경계를 명확히 넘는 방식(과잉설계 방지, 17.9-A).
"""
from __future__ import annotations

import time
from pathlib import Path


def write_heartbeat(path: Path) -> None:
    """메인 프로세스가 주기적으로 호출 — 파일에 현재 시각을 기록한다."""
    path.write_text(str(time.time()), encoding="utf-8")


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
