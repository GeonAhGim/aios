"""17번 — 알림 이력·설정 API 라우터 (FD-17.3/FD-17.4).

Spec: 기능설계문서_v1.20.md#FD-17.3/FD-17.4

FD-17.1(이벤트 발행)/17.2(발송 게이트웨이)는 다른 서비스가 이벤트
발생 시점에 내부적으로 호출하는 인프라(core/notifications/gateway.py)라
사용자 대면 HTTP 엔드포인트가 없다 — 여기서는 사용자가 직접 조회·설정하는
17.3(이력 조회)/17.4(수신설정)만 노출한다.
"""
from __future__ import annotations

from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends

from src.api.deps import get_current_user, get_pool
from src.core.notifications.history import NotificationHistoryEntry, list_notification_history
from src.core.notifications.preferences import (
    PreferenceUpdateResult,
    get_notification_preferences,
    update_notification_preferences,
)
from src.services.auth_service import User

router = APIRouter()


@router.get("/history")
async def get_history(
    event_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[NotificationHistoryEntry]:
    return await list_notification_history(
        pool, user.user_id, event_type=event_type, start=start, end=end
    )


@router.get("/preferences")
async def get_preferences(
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, bool]:
    return await get_notification_preferences(pool, user.user_id)


@router.put("/preferences")
async def put_preferences(
    changes: dict[str, bool],
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> PreferenceUpdateResult:
    return await update_notification_preferences(pool, user.user_id, changes)
