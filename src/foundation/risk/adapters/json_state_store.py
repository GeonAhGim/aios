"""U-15 PERSONAL 모드 상태 파일 저장소 — 단일 운영자 프로세스 로컬 상태
(kill switch·PAPER 시작일·위반 이력)를 JSON 파일 하나에 담는다.

이 리프는 마이그레이션 승인이 없어 새 DB 테이블을 만들지 않는다 — 재시작
후에도 상태가 남아야 하는 개인 운영 도구 특성상 파일 저장을 쓴다. 여러
프로세스의 동시쓰기는 가정하지 않는다(1인 운영자, 단일 프로세스) — 그런
환경이 필요해지면 postgres 어댑터로 교체한다(포트가 이미 분리돼 있어
호출부는 바뀌지 않는다).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path(__file__).resolve().parents[4] / "var" / "personal_mode_state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "kill_engaged": False,
        "kill_reason": None,
        "paper_started_on": None,
        "violations": [],
    }


class JsonPersonalStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else DEFAULT_STATE_PATH

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_state()
        data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        return data

    def _write(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self._path)

    async def is_kill_engaged(self) -> bool:
        return bool(self._read()["kill_engaged"])

    async def kill_reason(self) -> str | None:
        reason = self._read()["kill_reason"]
        return str(reason) if reason is not None else None

    async def engage_kill(self, *, reason: str) -> None:
        state = self._read()
        state["kill_engaged"] = True
        state["kill_reason"] = reason
        self._write(state)

    async def mark_paper_started_if_unset(self, *, today: date) -> date:
        state = self._read()
        raw = state.get("paper_started_on")
        if raw is not None:
            return date.fromisoformat(raw)
        state["paper_started_on"] = today.isoformat()
        self._write(state)
        return today

    async def record_violation(self, *, occurred_on: date) -> None:
        state = self._read()
        state["violations"].append(occurred_on.isoformat())
        self._write(state)

    async def violation_count_since(self, since: date) -> int:
        state = self._read()
        return sum(1 for raw in state["violations"] if date.fromisoformat(raw) >= since)

    async def violation_count_on(self, day: date) -> int:
        state = self._read()
        return sum(1 for raw in state["violations"] if date.fromisoformat(raw) == day)
