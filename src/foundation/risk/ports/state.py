"""U-15 PERSONAL mode process-state port — kill switch status + PAPER
operating history (start date, violation count). domain/application know
only this Protocol; the actual storage mechanism
(adapters/json_state_store.py) is unknown to them.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class PersonalOperationStatePort(Protocol):
    async def is_kill_engaged(self) -> bool: ...

    async def kill_reason(self) -> str | None: ...

    async def engage_kill(self, *, reason: str) -> None: ...

    async def mark_paper_started_if_unset(self, *, today: date) -> date:
        """If no PAPER start date is recorded yet, record `today` and
        return it (idempotent) — if one already exists, return the stored
        value unchanged."""
        ...

    async def record_violation(self, *, occurred_on: date) -> None: ...

    async def violation_count_since(self, since: date) -> int: ...

    async def violation_count_on(self, day: date) -> int: ...
