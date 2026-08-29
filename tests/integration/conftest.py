"""11.1 이후 공용 테스트 유틸.

users 테이블에 여러 테이블의 user_id FK가 연결된 이후, 그 테이블에 행을
넣는 통합 테스트는 실제 users 행이 먼저 있어야 한다(FK 위반 방지). 각
호출마다 고유 이메일로 별도 사용자를 만들어 테스트 간 격리를 유지한다.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg


async def create_test_user(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING user_id",
            f"test-{uuid4().hex}@example.com",
            "test-hash",
        )
    user_id: UUID = row["user_id"]
    return user_id


class NoopEventBus:
    """FastAPI 라우터 통합테스트 전용 EventBus 대역 — publish를 즉시 무시한다.

    main.py 실제 lifespan은 InProcessEventBus + NotificationGateway(발송기
    없음)를 쓰는데, 이 상태로 이벤트가 발행되면 게이트웨이가 매번 발송
    실패로 처리해 CRITICAL 재시도(최대 5회, 지수 백오프로 최대 31초, §5.5)가
    돈다 — lifespan 종료 시 event_bus.stop()이 그 재시도가 끝날 때까지
    기다리므로 이벤트를 발행하는 모든 라우터 테스트가 수십 초씩 걸리게
    된다. `app.dependency_overrides[get_event_bus] = lambda: NoopEventBus()`
    로 교체해 "이벤트가 발행됐는지"만 확인하고 재시도 경로는 피한다."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> None:
        self.published.append((topic, payload))

    def subscribe(self, topic, handler, *, criticality) -> None:  # noqa: ANN001
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None
