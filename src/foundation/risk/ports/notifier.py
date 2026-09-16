"""U-15 텔레그램 등 개인 운영 알림 포트. domain/application은 이 Protocol만
알고, 실제 전송 수단(adapters/telegram_adapter.py)은 모른다.
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
