"""U-15 PERSONAL mode state file store — a single JSON file holding the
local process state (kill switch, PAPER start date, violation history) for
a single-operator process.

This leaf has no migration approval, so it does not create a new DB
table — file storage is used because a personal operating tool needs state
to survive restarts. Concurrent writes from multiple processes are not
assumed (single operator, single process) — if that ever becomes
necessary, swap in a Postgres adapter (the port is already separated, so
callers do not change).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

DEFAULT_STATE_PATH = Path(__file__).resolve().parents[4] / "var" / "personal_mode_state.json"

# task-3986 -- same convention as promotion_checklist.py's
# PERSONAL_ADR_0829E_CONDITION2_MET: an operational fact this single-operator
# tool cannot discover on its own, decided by an explicit .env slot (fail-closed
# default: unset/empty means personal mode is not scoped to any account, so
# foundation_gate.py's 4th layer never activates).
_PERSONAL_MODE_ACCOUNT_ENV = "PERSONAL_MODE_ACCOUNT_ID"


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

    async def personal_mode_account_id(self) -> UUID | None:
        raw = os.environ.get(_PERSONAL_MODE_ACCOUNT_ENV, "").strip()
        return UUID(raw) if raw else None

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
