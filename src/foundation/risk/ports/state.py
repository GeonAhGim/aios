"""U-15 PERSONAL mode process-state port — kill switch status + PAPER
operating history (start date, violation count). domain/application know
only this Protocol; the actual storage mechanism
(adapters/json_state_store.py) is unknown to them.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID


class PersonalOperationStatePort(Protocol):
    async def personal_mode_account_id(self) -> UUID | None:
        """The single account/tenant id personal-conservative mode is
        scoped to for this deployment, or `None` if not configured/enabled.
        task-3986 — `foundation_gate.py`'s 4th layer only evaluates orders
        for the account matching this id; every other account's existing
        3-layer flow is untouched (no global enforcement, per-account
        opt-in only)."""
        ...

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
