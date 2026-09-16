"""U-15 personal-operator notification port (Telegram etc). domain/
application know only this Protocol; the actual delivery mechanism
(adapters/telegram_adapter.py) is unknown to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class PersonalNotificationKind(str, Enum):
    FILL = "FILL"
    LIMIT_BREACH = "LIMIT_BREACH"
    KILL_SWITCH = "KILL_SWITCH"


@dataclass(frozen=True)
class PersonalNotification:
    kind: PersonalNotificationKind
    message: str


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    status_code: int | None
    error: str | None


class PersonalNotifierPort(Protocol):
    async def send(self, notification: PersonalNotification) -> NotifyResult: ...
