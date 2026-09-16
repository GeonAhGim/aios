"""U-15 PERSONAL 모드 프로세스 상태 포트 — kill switch 여부 + PAPER 운영
이력(시작일·위반 건수). domain/application은 이 Protocol만 알고 실제 저장
방식(adapters/json_state_store.py)은 모른다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class PersonalOperationStatePort(Protocol):
    async def is_kill_engaged(self) -> bool: ...

    async def kill_reason(self) -> str | None: ...

    async def engage_kill(self, *, reason: str) -> None: ...

    async def mark_paper_started_if_unset(self, *, today: date) -> date:
        """PAPER 시작일이 아직 없으면 `today`로 기록하고 반환한다(멱등) —
        이미 있으면 저장된 값을 그대로 반환한다."""
        ...

    async def record_violation(self, *, occurred_on: date) -> None: ...

    async def violation_count_since(self, since: date) -> int: ...

    async def violation_count_on(self, day: date) -> int: ...
